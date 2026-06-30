import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.db.session import AsyncSessionLocal
from app.schemas.ai_request_schema import (
    AiRequestSnapshot,
    EligibilityResultResponse,
    EligibilityRequestCreate,
    RecommendationAnswerSubmit,
    RecommendationHistoryResponse,
    RecommendationPollingResponse,
    RecommendationRequestCreate,
)
from app.schemas.ai_contract import RequestStatus
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService
from app.services.chat.chat_service import ChatService


# AI 단계(파싱·판정·리랭크)가 충분히 생각할 수 있도록 넉넉하게 둔다.
# 각 LLM 단계 타임아웃 합(파싱 60 + 판정 90 + 근거검색 20 + 리랭크 180)보다 크게.
AI_BACKGROUND_TIMEOUT_SECONDS = 360
AI_REQUEST_USER_ERROR_MESSAGE = (
    "분석 처리 중 일시적인 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)
logger = logging.getLogger(__name__)

recommendation_router = APIRouter(
    prefix="/api/v1/recommendations",
    tags=["AI Recommendations"],
)
eligibility_router = APIRouter(
    prefix="/api/v1/eligibility",
    tags=["AI Eligibility"],
)


def _request_meta(snapshot: AiRequestSnapshot) -> dict[str, object]:
    return {
        "request_id": snapshot.request_id,
        "follow_up_required": snapshot.status.value == "FOLLOW_UP_REQUIRED",
    }


def _recommendation_polling_meta(
    response: RecommendationPollingResponse,
) -> dict[str, object]:
    return {
        "request_id": response.request_id,
        "follow_up_required": bool(response.follow_up_questions),
    }


def _eligibility_result_meta(
    response: EligibilityResultResponse,
) -> dict[str, object]:
    return {
        "request_id": response.request_id,
        "follow_up_required": bool(response.follow_up_questions),
    }


def _eligibility_source_ref(
    chat_session_id: int | None,
    source_ref_id: str | None,
) -> str | None:
    if not chat_session_id:
        return source_ref_id
    chat_ref = f"chat_session:{chat_session_id}"
    if source_ref_id:
        return f"{chat_ref};source:{source_ref_id}"
    return chat_ref


async def process_ai_condition_request(request_type: str, request_id: int) -> None:
    service = AiRequestLifecycleService()
    try:
        async with AsyncSessionLocal() as db:
            try:
                logger.info(
                    "AI background task started: request_type=%s request_id=%s",
                    request_type,
                    request_id,
                )
                await db.execute(text("SET LOCAL lock_timeout = '5s'"))
                await db.execute(text("SET LOCAL statement_timeout = '60s'"))
                await asyncio.wait_for(
                    service.process_condition_request(
                        db=db,
                        request_type=request_type,
                        request_id=request_id,
                    ),
                    timeout=AI_BACKGROUND_TIMEOUT_SECONDS,
                )
                await db.commit()
                logger.info(
                    "AI background task completed: request_type=%s request_id=%s",
                    request_type,
                    request_id,
                )
            except Exception:
                await db.rollback()
                raise
    except TimeoutError:
        logger.exception(
            "AI background task timed out: request_type=%s request_id=%s",
            request_type,
            request_id,
        )
        await _mark_ai_request_failed(
            request_type,
            request_id,
            AI_REQUEST_USER_ERROR_MESSAGE,
        )
    except Exception:
        logger.exception(
            "AI background task failed: request_type=%s request_id=%s",
            request_type,
            request_id,
        )
        await _mark_ai_request_failed(
            request_type,
            request_id,
            AI_REQUEST_USER_ERROR_MESSAGE,
        )


async def _mark_ai_request_failed(
    request_type: str,
    request_id: int,
    error_message: str,
) -> None:
    async with AsyncSessionLocal() as db:
        service = AiRequestLifecycleService()
        try:
            await service.mark_failed(
                db=db,
                request_type=request_type,
                request_id=request_id,
                error_message=error_message,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "Failed to mark AI request as failed: request_type=%s request_id=%s",
                request_type,
                request_id,
            )


@recommendation_router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def create_recommendation_request(
    payload: RecommendationRequestCreate,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    service = AiRequestLifecycleService()
    snapshot = await service.create_request(
        db=db,
        user_id=current_user.user_id,
        request_type="recommendation",
        source_type=payload.source_type,
        source_ref_id=payload.source_ref_id,
        raw_query=payload.raw_query,
        selected_conditions=payload.selected_conditions,
    )
    snapshot = await service.mark_processing(
        db=db,
        request_type="recommendation",
        request_id=int(snapshot.request_id),
    )
    await db.commit()
    background_tasks.add_task(
        process_ai_condition_request,
        "recommendation",
        int(snapshot.request_id),
    )
    return success_response(
        data=snapshot,
        status_code=status.HTTP_202_ACCEPTED,
        meta=_request_meta(snapshot),
    )


@recommendation_router.post(
    "/requests/{request_id}/answers",
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_recommendation_answers(
    request_id: int,
    payload: RecommendationAnswerSubmit,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    # 추가질문 답변(또는 건너뛰기) → 조건 반영 후 추천 재실행.
    service = AiRequestLifecycleService()
    snapshot = await service.submit_recommendation_answers(
        db=db,
        request_id=request_id,
        user_id=current_user.user_id,
        answers=[answer.model_dump() for answer in payload.answers],
    )
    await db.commit()
    background_tasks.add_task(
        process_ai_condition_request,
        "recommendation",
        int(snapshot.request_id),
    )
    return success_response(
        data=snapshot,
        status_code=status.HTTP_202_ACCEPTED,
        meta=_request_meta(snapshot),
    )


@recommendation_router.get("/requests")
async def list_recommendation_history(
    db: DbSessionDep,
    current_user: CurrentUserDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    # 로그인 사용자의 완료된 추천 이력(최신순).
    service = AiRequestLifecycleService()
    response = await service.get_recommendation_history(
        db=db,
        user_id=current_user.user_id,
        limit=limit,
    )
    return success_response(data=response)


@recommendation_router.get("/requests/{request_id}")
async def get_recommendation_request(
    request_id: int,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    service = AiRequestLifecycleService()
    response = await service.get_recommendation_polling_result(
        db=db,
        request_id=request_id,
        user_id=current_user.user_id,
    )
    return success_response(data=response, meta=_recommendation_polling_meta(response))


@eligibility_router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
async def create_eligibility_request(
    payload: EligibilityRequestCreate,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    service = AiRequestLifecycleService()
    if payload.chat_session_id is not None:
        await ChatService.ensure_owned_session(
            db,
            user_id=current_user.user_id,
            chat_session_id=payload.chat_session_id,
        )
    snapshot = await service.create_eligibility_request(
        db=db,
        user_id=current_user.user_id,
        policy_identifier=payload.policy_id,
        source_type=payload.source_type,
        source_ref_id=_eligibility_source_ref(
            payload.chat_session_id,
            payload.source_ref_id,
        ),
        raw_query=payload.raw_query,
        selected_conditions=payload.selected_conditions,
    )
    snapshot = await service.mark_processing(
        db=db,
        request_type="eligibility",
        request_id=int(snapshot.request_id),
    )
    await db.commit()
    background_tasks.add_task(
        process_ai_condition_request,
        "eligibility",
        int(snapshot.request_id),
    )
    return success_response(
        data=snapshot,
        status_code=status.HTTP_202_ACCEPTED,
        meta=_request_meta(snapshot),
    )


@eligibility_router.get("/requests/{request_id}")
async def get_eligibility_request(
    request_id: int,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    service = AiRequestLifecycleService()
    response = await service.get_eligibility_result(
        db=db,
        request_id=request_id,
        user_id=current_user.user_id,
    )
    if response.status not in {RequestStatus.READY, RequestStatus.PROCESSING}:
        try:
            request = await service.repository.find_by_id(
                db,
                request_type="eligibility",
                request_id=request_id,
            )
            if request is not None:
                await ChatService.persist_eligibility_result_message(
                    db,
                    user_id=current_user.user_id,
                    request=request,
                    result_json=response.model_dump(mode="json"),
                )
                await db.commit()
        except Exception:
            if hasattr(db, "rollback"):
                await db.rollback()
            logger.exception(
                "Failed to persist eligibility result to chat session: request_id=%s",
                request_id,
            )
    return success_response(data=response, meta=_eligibility_result_meta(response))

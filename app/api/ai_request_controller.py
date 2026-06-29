import asyncio
import logging

from fastapi import APIRouter, BackgroundTasks, status
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
    RecommendationPollingResponse,
    RecommendationRequestCreate,
)
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


# AI 단계(파싱·판정·리랭크)가 충분히 생각할 수 있도록 넉넉하게 둔다.
# 각 LLM 단계 타임아웃 합(파싱 60 + 판정 90 + 근거검색 20 + 리랭크 180)보다 크게.
AI_BACKGROUND_TIMEOUT_SECONDS = 360
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
        error_message = (
            "AI request processing timed out after "
            f"{AI_BACKGROUND_TIMEOUT_SECONDS} seconds"
        )
        logger.exception(
            "AI background task timed out: request_type=%s request_id=%s",
            request_type,
            request_id,
        )
        await _mark_ai_request_failed(request_type, request_id, error_message)
    except Exception as exc:
        logger.exception(
            "AI background task failed: request_type=%s request_id=%s",
            request_type,
            request_id,
        )
        await _mark_ai_request_failed(request_type, request_id, str(exc))


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
    snapshot = await service.create_eligibility_request(
        db=db,
        user_id=current_user.user_id,
        policy_identifier=payload.policy_id,
        source_type=payload.source_type,
        source_ref_id=payload.source_ref_id,
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
    return success_response(data=response, meta=_eligibility_result_meta(response))

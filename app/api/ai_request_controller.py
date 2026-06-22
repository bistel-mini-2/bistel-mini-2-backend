from fastapi import APIRouter, BackgroundTasks, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.db.session import AsyncSessionLocal
from app.schemas.ai_request_schema import (
    AiRequestSnapshot,
    EligibilityRequestCreate,
    RecommendationPollingResponse,
    RecommendationRequestCreate,
)
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


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


async def process_ai_condition_request(request_type: str, request_id: int) -> None:
    async with AsyncSessionLocal() as db:
        service = AiRequestLifecycleService()
        try:
            await service.process_condition_request(
                db=db,
                request_type=request_type,
                request_id=request_id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


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
    snapshot = await service.get_request(
        db=db,
        request_type="eligibility",
        request_id=request_id,
        user_id=current_user.user_id,
    )
    return success_response(data=snapshot, meta=_request_meta(snapshot))

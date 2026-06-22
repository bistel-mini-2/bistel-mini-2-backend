from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, status
from fastapi.responses import JSONResponse

from app.common.policy_types import LifeStage, RegionCode
from app.common.response import paginated_response, success_response
from app.common.schemas import ApiResponse
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.db.session import AsyncSessionLocal
from app.schemas.policy_eligibility_schema import (
    PolicyEligibilityRequestCreate,
    PolicyEligibilityRequestResponse,
)
from app.schemas.policy_schema import PolicyListItemResponse, PolicySort
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService
from app.services.policy_service import PolicyServiceDep


router = APIRouter(prefix="/api/v1/policies", tags=["Policies"])


async def process_policy_eligibility_request(request_id: int) -> None:
    async with AsyncSessionLocal() as db:
        service = AiRequestLifecycleService()
        try:
            await service.process_condition_request(
                db=db,
                request_type="eligibility",
                request_id=request_id,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@router.get(
    "",
    response_model=ApiResponse[list[PolicyListItemResponse]],
    summary="정책 목록 조회",
)
async def get_policy_list(
    db: DbSessionDep,
    service: PolicyServiceDep,
    query: Annotated[str | None, Query(max_length=200)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=100)] = None,
    tags: Annotated[list[str] | None, Query()] = None,
    region_code: Annotated[RegionCode | None, Query()] = None,
    region: Annotated[RegionCode | None, Query()] = None,
    stage: Annotated[LifeStage | None, Query()] = None,
    sort: Annotated[PolicySort, Query()] = PolicySort.UPDATED_AT,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JSONResponse:
    items, total = await service.get_policy_list(
        db,
        query=query if query is not None else q,
        category=category,
        tags=tags,
        region_code=(
            region_code.value
            if region_code is not None
            else region.value if region is not None else None
        ),
        stage=stage.value if stage is not None else None,
        sort=sort,
        page=page,
        size=size,
    )
    return paginated_response(
        data=items,
        page=page,
        size=size,
        total=total,
    )


@router.post(
    "/{policy_slug}/eligibility",
    response_model=ApiResponse[PolicyEligibilityRequestResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="지원 가능성 분석 요청 생성",
)
async def create_policy_eligibility_request(
    policy_slug: str,
    payload: PolicyEligibilityRequestCreate,
    background_tasks: BackgroundTasks,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    service = AiRequestLifecycleService()
    snapshot = await service.create_eligibility_request(
        db=db,
        user_id=current_user.user_id,
        policy_identifier=policy_slug,
        source_type="POLICY_DETAIL",
        selected_conditions=payload.user_conditions,
    )
    snapshot = await service.mark_processing(
        db=db,
        request_type="eligibility",
        request_id=int(snapshot.request_id),
    )
    await db.commit()
    background_tasks.add_task(
        process_policy_eligibility_request,
        int(snapshot.request_id),
    )
    return success_response(
        data=PolicyEligibilityRequestResponse(
            request_id=snapshot.request_id,
            status="loading",
        ),
        status_code=status.HTTP_202_ACCEPTED,
        meta={
            "request_id": snapshot.request_id,
            "policy_slug": policy_slug,
        },
    )

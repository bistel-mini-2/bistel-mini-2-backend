from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.policy_types import LifeStage, RegionCode
from app.common.response import paginated_response
from app.common.schemas import ApiResponse
from app.core.dependencies import DbSessionDep
from app.schemas.policy_schema import PolicyListItemResponse, PolicySort
from app.services.policy_service import PolicyServiceDep


router = APIRouter(prefix="/api/v1/policies", tags=["Policies"])


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

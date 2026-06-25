from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.common.schemas import ApiResponse, PaginationMeta
from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    OptionalCurrentUserDep,
)
from app.schemas.compare_schema import (
    CompareHistoryListResponse,
    PolicyCompareResponse,
)
from app.services.compare_service import CompareServiceDep


router = APIRouter(prefix="/api/v1/compare", tags=["Policy Compare"])
public_router = APIRouter(prefix="/compare", tags=["Policy Compare"])
history_router = APIRouter(
    prefix="/api/v1/users/me/compare-history",
    tags=["Policy Compare"],
)


async def _compare_policies(
    db: DbSessionDep,
    service: CompareServiceDep,
    a: str,
    b: str,
    current_user: OptionalCurrentUserDep,
) -> JSONResponse:
    result = await service.compare_policies(
        db,
        slug_a=a,
        slug_b=b,
        user_id=current_user.user_id if current_user is not None else None,
    )
    return success_response(data=result)


@router.get("", response_model=ApiResponse[PolicyCompareResponse])
async def compare_policies(
    db: DbSessionDep,
    service: CompareServiceDep,
    current_user: OptionalCurrentUserDep,
    a: Annotated[str, Query(min_length=1, max_length=100)],
    b: Annotated[str, Query(min_length=1, max_length=100)],
) -> JSONResponse:
    return await _compare_policies(db, service, a, b, current_user)


@public_router.get("", response_model=ApiResponse[PolicyCompareResponse])
async def compare_policies_alias(
    db: DbSessionDep,
    service: CompareServiceDep,
    current_user: OptionalCurrentUserDep,
    a: Annotated[str, Query(min_length=1, max_length=100)],
    b: Annotated[str, Query(min_length=1, max_length=100)],
) -> JSONResponse:
    return await _compare_policies(db, service, a, b, current_user)


@history_router.get("", response_model=ApiResponse[CompareHistoryListResponse])
async def get_my_compare_history(
    db: DbSessionDep,
    service: CompareServiceDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JSONResponse:
    items, total = await service.get_compare_history(
        db,
        user_id=current_user.user_id,
        page=page,
        size=size,
    )
    meta = PaginationMeta(
        page=page,
        size=size,
        total=total,
        total_pages=(total + size - 1) // size if size > 0 else 0,
    )
    return success_response(
        data=CompareHistoryListResponse(items=items),
        meta=meta.model_dump(),
    )

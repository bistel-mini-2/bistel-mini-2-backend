import math
from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.common.schemas import ApiResponse, PaginationMeta
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.favorite_schema import (
    FavoriteCreateResponse,
    FavoriteDeleteResponse,
    FavoriteListResponse,
)
from app.services.favorite_service import FavoriteService


favorites_router = APIRouter(
    prefix="/api/v1/favorites",
    tags=["Favorites"],
)
user_favorites_router = APIRouter(
    prefix="/api/v1/users/me/favorites",
    tags=["Favorites"],
)


@favorites_router.post(
    "/{policy_slug}",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[FavoriteCreateResponse],
    summary="관심 정책 저장",
)
async def add_favorite(
    policy_slug: str,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await FavoriteService.add(
        db,
        user_id=current_user.user_id,
        policy_slug=policy_slug,
    )
    return success_response(
        data=response,
        status_code=status.HTTP_201_CREATED,
    )


@favorites_router.delete(
    "/{policy_slug}",
    response_model=ApiResponse[FavoriteDeleteResponse],
    summary="관심 정책 해제",
)
async def remove_favorite(
    policy_slug: str,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await FavoriteService.remove(
        db,
        user_id=current_user.user_id,
        policy_slug=policy_slug,
    )
    return success_response(data=response)


@user_favorites_router.get(
    "",
    response_model=ApiResponse[FavoriteListResponse],
    summary="관심 정책 목록 조회",
)
async def get_my_favorites(
    db: DbSessionDep,
    current_user: CurrentUserDep,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> JSONResponse:
    items, total = await FavoriteService.get_list(
        db,
        user_id=current_user.user_id,
        page=page,
        size=size,
    )
    meta = PaginationMeta(
        page=page,
        size=size,
        total=total,
        total_pages=math.ceil(total / size) if size > 0 else 0,
    )
    return success_response(
        data=FavoriteListResponse(items=items),
        meta=meta.model_dump(),
    )

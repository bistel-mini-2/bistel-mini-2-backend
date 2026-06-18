from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.common.schemas import ApiResponse
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.family_profile_schema import (
    FamilyProfileRequest,
    FamilyProfileResponse,
)
from app.services.family_profile_service import FamilyProfileService


ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {
        "model": ApiResponse[None],
        "description": "Invalid or missing credentials",
    },
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": ApiResponse[None],
        "description": "Validation error",
    },
}

router = APIRouter(prefix="/api/v1/family-profiles", tags=["Family Profiles"])


@router.get(
    "/me",
    response_model=ApiResponse[FamilyProfileResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
    },
    summary="내 가족 프로필 조회",
)
async def get_my_family_profile(
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    result = await FamilyProfileService.get_my_family_profile(db, current_user)
    return success_response(data=result)


@router.put(
    "/me",
    response_model=ApiResponse[FamilyProfileResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_422_UNPROCESSABLE_ENTITY: ERROR_RESPONSES[
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ],
    },
    summary="내 가족 프로필 저장",
)
async def save_my_family_profile(
    request: FamilyProfileRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    result = await FamilyProfileService.save_my_family_profile(
        db,
        current_user,
        request,
    )
    return success_response(data=result)

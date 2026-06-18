from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.schemas import ApiResponse
from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.auth_schema import LoginRequest, SignUpRequest, TokenResponse
from app.schemas.user_schema import (
    UserNicknameUpdateRequest,
    UserPasswordUpdateRequest,
    UserPasswordUpdateResponse,
    UserResponse,
)
from app.services.auth_service import AuthService
from app.services.user_service import UserService


ERROR_RESPONSES: dict[int, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {
        "model": ApiResponse[None],
        "description": "Invalid request",
    },
    status.HTTP_401_UNAUTHORIZED: {
        "model": ApiResponse[None],
        "description": "Invalid or missing credentials",
    },
    status.HTTP_409_CONFLICT: {
        "model": ApiResponse[None],
        "description": "Conflicting user data",
    },
    status.HTTP_422_UNPROCESSABLE_ENTITY: {
        "model": ApiResponse[None],
        "description": "Validation error",
    },
}

auth_router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
users_router = APIRouter(prefix="/api/v1/users", tags=["Users"])


@auth_router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[TokenResponse],
    responses={
        status.HTTP_409_CONFLICT: ERROR_RESPONSES[status.HTTP_409_CONFLICT],
        status.HTTP_422_UNPROCESSABLE_ENTITY: ERROR_RESPONSES[
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ],
    },
    summary="회원가입",
)
async def sign_up(
    request: SignUpRequest,
    db: DbSessionDep,
) -> JSONResponse:
    access_token, user = await AuthService.sign_up(
        db,
        email=request.email,
        password=request.password,
        nickname=request.nickname,
    )
    return success_response(
        data=TokenResponse(
            access_token=access_token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        ),
        status_code=status.HTTP_201_CREATED,
    )


@auth_router.post(
    "/login",
    response_model=ApiResponse[TokenResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_422_UNPROCESSABLE_ENTITY: ERROR_RESPONSES[
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ],
    },
    summary="로그인",
)
async def login(
    request: LoginRequest,
    db: DbSessionDep,
) -> JSONResponse:
    access_token, user = await AuthService.login(
        db,
        email=request.email,
        password=request.password,
    )
    return success_response(
        data=TokenResponse(
            access_token=access_token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        )
    )


@users_router.get(
    "/me",
    response_model=ApiResponse[UserResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
    },
    summary="내 정보 조회",
)
async def get_me(current_user: CurrentUserDep) -> JSONResponse:
    return success_response(data=UserResponse.model_validate(current_user))


@users_router.patch(
    "/me",
    response_model=ApiResponse[UserResponse],
    responses={
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_409_CONFLICT: ERROR_RESPONSES[status.HTTP_409_CONFLICT],
        status.HTTP_422_UNPROCESSABLE_ENTITY: ERROR_RESPONSES[
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ],
    },
    summary="내 계정 정보 수정",
)
async def update_me(
    request: UserNicknameUpdateRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    user = await UserService.update_nickname(
        db,
        current_user,
        request.nickname,
    )
    return success_response(data=UserResponse.model_validate(user))


@users_router.put(
    "/me/password",
    response_model=ApiResponse[UserPasswordUpdateResponse],
    responses={
        status.HTTP_400_BAD_REQUEST: ERROR_RESPONSES[status.HTTP_400_BAD_REQUEST],
        status.HTTP_401_UNAUTHORIZED: ERROR_RESPONSES[status.HTTP_401_UNAUTHORIZED],
        status.HTTP_422_UNPROCESSABLE_ENTITY: ERROR_RESPONSES[
            status.HTTP_422_UNPROCESSABLE_ENTITY
        ],
    },
    summary="내 비밀번호 변경",
)
async def update_my_password(
    request: UserPasswordUpdateRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    await UserService.update_password(
        db,
        current_user,
        request.current_password,
        request.new_password,
    )
    return success_response(data=UserPasswordUpdateResponse(changed=True))

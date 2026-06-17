from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.auth_schema import LoginRequest, SignUpRequest, TokenResponse
from app.schemas.user_schema import UserResponse
from app.services.auth_service import AuthService


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def sign_up(
    request: SignUpRequest,
    db: DbSessionDep,
) -> JSONResponse:
    user = await AuthService.sign_up(
        db,
        email=request.email,
        password=request.password,
        nickname=request.nickname,
    )
    return success_response(
        data=UserResponse.model_validate(user),
        status_code=status.HTTP_201_CREATED,
    )


@router.post("/login")
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


@router.get("/me")
async def get_me(current_user: CurrentUserDep) -> JSONResponse:
    return success_response(data=UserResponse.model_validate(current_user))

from fastapi import APIRouter, status

from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.auth_schema import LoginRequest, SignUpRequest, SignUpResponse, TokenResponse
from app.schemas.user_schema import UserResponse
from app.services.auth_service import AuthService


router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/signup",
    response_model=SignUpResponse,
    status_code=status.HTTP_201_CREATED,
)
async def sign_up(
    request: SignUpRequest,
    db: DbSessionDep,
) -> SignUpResponse:
    user = await AuthService.sign_up(
        db,
        email=request.email,
        password=request.password,
        nickname=request.nickname,
    )
    return SignUpResponse(
        user=UserResponse.model_validate(user),
        message="signup success",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: LoginRequest,
    db: DbSessionDep,
) -> TokenResponse:
    access_token, user = await AuthService.login(
        db,
        email=request.email,
        password=request.password,
    )
    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)

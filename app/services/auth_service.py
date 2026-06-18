from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import AppException, ErrorCode
from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models.user import User
from app.repositories.user_repository import UserRepository


class AuthService:
    @staticmethod
    async def validate_sign_up(
        db: AsyncSession,
        email: str,
        nickname: str,
    ) -> None:
        normalized_email = email.strip().lower()
        normalized_nickname = nickname.strip()

        if await UserRepository.find_by_email(db, normalized_email) is not None:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.EMAIL_ALREADY_EXISTS,
                message="Email already registered",
            )

        if await UserRepository.find_by_nickname(db, normalized_nickname) is not None:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.NICKNAME_ALREADY_EXISTS,
                message="Nickname already registered",
            )

    @staticmethod
    async def sign_up(
        db: AsyncSession,
        email: str,
        password: str,
        nickname: str,
    ) -> tuple[str, User]:
        normalized_email = email.strip().lower()
        normalized_nickname = nickname.strip()

        await AuthService.validate_sign_up(db, normalized_email, normalized_nickname)

        user = User(
            email=normalized_email,
            password_hash=get_password_hash(password),
            nickname=normalized_nickname,
            role="USER",
        )
        try:
            saved_user = await UserRepository.save(db, user)
        except IntegrityError as exc:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.CONFLICT,
                message="Email or nickname already registered",
            ) from exc

        access_token = create_access_token(subject=saved_user.user_id)
        return access_token, saved_user

    @staticmethod
    async def login(db: AsyncSession, email: str, password: str) -> tuple[str, User]:
        user = await UserRepository.find_by_email(db, email.strip().lower())
        if user is None or not verify_password(password, user.password_hash):
            raise AppException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=ErrorCode.INVALID_CREDENTIALS,
                message="Invalid email or password",
            )

        access_token = create_access_token(subject=user.user_id)
        return access_token, user

    @staticmethod
    async def get_current_user_by_id(db: AsyncSession, user_id: int) -> User:
        user = await UserRepository.find_by_id(db, user_id)
        if user is None:
            raise AppException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                code=ErrorCode.UNAUTHORIZED,
                message="Invalid authentication credentials",
            )

        return user

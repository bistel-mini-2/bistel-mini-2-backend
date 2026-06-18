from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import AppException, ErrorCode
from app.core.security import get_password_hash, verify_password
from app.db.models.user import User
from app.repositories.user_repository import UserRepository


class UserService:
    @staticmethod
    async def update_nickname(
        db: AsyncSession,
        user: User,
        nickname: str,
    ) -> User:
        normalized_nickname = nickname.strip()
        existing_user = await UserRepository.find_by_nickname(
            db,
            normalized_nickname,
        )

        if existing_user is not None and existing_user.user_id != user.user_id:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.DUPLICATE_NICKNAME,
                message="Nickname already registered",
            )

        user.nickname = normalized_nickname
        try:
            return await UserRepository.save(db, user)
        except IntegrityError as exc:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.DUPLICATE_NICKNAME,
                message="Nickname already registered",
            ) from exc

    @staticmethod
    async def update_password(
        db: AsyncSession,
        user: User,
        current_password: str,
        new_password: str,
    ) -> None:
        if not verify_password(current_password, user.password_hash):
            raise AppException(
                status_code=status.HTTP_400_BAD_REQUEST,
                code=ErrorCode.INVALID_CREDENTIALS,
                message="Invalid current password",
            )

        user.password_hash = get_password_hash(new_password)
        await UserRepository.save(db, user)

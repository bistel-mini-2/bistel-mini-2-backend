from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, get_password_hash, verify_password
from app.db.models.user import User
from app.repositories.user_repository import UserRepository


class AuthService:
    @staticmethod
    async def sign_up(
        db: AsyncSession,
        email: str,
        password: str,
        nickname: str,
    ) -> User:
        existing_email_user = await UserRepository.find_by_email(db, email)
        if existing_email_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email already registered",
            )

        existing_nickname_user = await UserRepository.find_by_nickname(
            db,
            nickname,
        )
        if existing_nickname_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Nickname already registered",
            )

        user = User(
            email=email,
            password_hash=get_password_hash(password),
            nickname=nickname,
            role="USER",
        )
        return await UserRepository.save(db, user)

    @staticmethod
    async def login(db: AsyncSession, email: str, password: str) -> tuple[str, User]:
        user = await UserRepository.find_by_email(db, email)
        if user is None or not verify_password(password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        access_token = create_access_token(subject=user.user_id)
        return access_token, user

    @staticmethod
    async def get_current_user_by_id(db: AsyncSession, user_id: int) -> User:
        user = await UserRepository.find_by_id(db, user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        return user

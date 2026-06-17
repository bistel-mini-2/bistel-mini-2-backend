from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


class UserRepository:
    @staticmethod
    async def find_by_email(db: AsyncSession, email: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def find_by_nickname(db: AsyncSession, nickname: str) -> User | None:
        result = await db.execute(select(User).where(User.nickname == nickname))
        return result.scalar_one_or_none()

    @staticmethod
    async def find_by_id(db: AsyncSession, user_id: int) -> User | None:
        result = await db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def save(db: AsyncSession, user: User) -> User:
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

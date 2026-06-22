from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User


class UserRepository:
    @staticmethod
    async def ensure_user_schema(db: AsyncSession) -> None:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id bigserial PRIMARY KEY,
                    email varchar(255) NOT NULL,
                    password_hash varchar(255) NOT NULL,
                    nickname varchar(100) NOT NULL,
                    role varchar(20) NOT NULL,
                    created_at timestamp DEFAULT CURRENT_TIMESTAMP,
                    updated_at timestamp DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        for statement in [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email varchar(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash varchar(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname varchar(100)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role varchar(20)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS created_at timestamp DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS updated_at timestamp DEFAULT CURRENT_TIMESTAMP",
            "CREATE UNIQUE INDEX IF NOT EXISTS users_email_idx ON users (email)",
            "CREATE UNIQUE INDEX IF NOT EXISTS users_nickname_idx ON users (nickname)",
        ]:
            await db.execute(text(statement))

    @staticmethod
    async def find_by_email(db: AsyncSession, email: str) -> User | None:
        await UserRepository.ensure_user_schema(db)
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    @staticmethod
    async def find_by_nickname(db: AsyncSession, nickname: str) -> User | None:
        await UserRepository.ensure_user_schema(db)
        result = await db.execute(select(User).where(User.nickname == nickname))
        return result.scalar_one_or_none()

    @staticmethod
    async def find_by_id(db: AsyncSession, user_id: int) -> User | None:
        await UserRepository.ensure_user_schema(db)
        result = await db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def save(db: AsyncSession, user: User) -> User:
        await UserRepository.ensure_user_schema(db)
        db.add(user)
        await db.flush()
        await db.refresh(user)
        return user

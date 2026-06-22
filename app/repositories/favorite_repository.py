from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.policy import Policy
from app.db.models.user_favorite import UserFavorite


class FavoriteRepository:
    @staticmethod
    async def find_policy_by_slug(
        db: AsyncSession,
        policy_slug: str,
    ) -> Policy | None:
        result = await db.execute(
            select(Policy).where(
                Policy.policy_code == policy_slug,
                Policy.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_favorite(
        db: AsyncSession,
        user_id: int,
        policy_id: int,
    ) -> UserFavorite | None:
        result = await db.execute(
            select(UserFavorite).where(
                UserFavorite.user_id == user_id,
                UserFavorite.policy_id == policy_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def save(
        db: AsyncSession,
        favorite: UserFavorite,
    ) -> UserFavorite:
        db.add(favorite)
        await db.flush()
        await db.refresh(favorite)
        return favorite

    @staticmethod
    async def delete(
        db: AsyncSession,
        favorite: UserFavorite,
    ) -> None:
        await db.delete(favorite)
        await db.flush()

    @staticmethod
    async def find_list(
        db: AsyncSession,
        *,
        user_id: int,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        count_result = await db.execute(
            select(func.count())
            .select_from(UserFavorite)
            .join(Policy, Policy.policy_id == UserFavorite.policy_id)
            .where(
                UserFavorite.user_id == user_id,
                Policy.is_active.is_(True),
            )
        )
        total = int(count_result.scalar_one())

        result = await db.execute(
            select(
                Policy.policy_id,
                Policy.policy_code.label("policy_slug"),
                Policy.policy_name,
                Policy.main_category.label("category"),
                Policy.region_scope,
                Policy.region_code,
                UserFavorite.saved_at,
            )
            .join(Policy, Policy.policy_id == UserFavorite.policy_id)
            .where(
                UserFavorite.user_id == user_id,
                Policy.is_active.is_(True),
            )
            .order_by(
                UserFavorite.saved_at.desc(),
                UserFavorite.policy_id.desc(),
            )
            .limit(size)
            .offset((page - 1) * size)
        )
        return [dict(row) for row in result.mappings().all()], total

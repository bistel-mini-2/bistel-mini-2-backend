import os
import asyncio

import pytest
from fastapi import status
from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppException, ErrorCode
from app.db.models.policy import Policy
from app.db.models.user import User
from app.db.models.user_favorite import UserFavorite
from app.db.session import engine
from app.services.favorite_service import FavoriteService


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Set RUN_POSTGRES_INTEGRATION=1 to test the configured PostgreSQL",
)


def test_favorite_flow_with_configured_postgresql() -> None:
    if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def run_test() -> None:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            db = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                pair = (
                    await db.execute(
                        select(User.user_id, Policy.policy_code)
                        .select_from(User)
                        .join(Policy, true())
                        .join(
                            UserFavorite,
                            (UserFavorite.user_id == User.user_id)
                            & (UserFavorite.policy_id == Policy.policy_id),
                            isouter=True,
                        )
                        .where(
                            Policy.is_active.is_(True),
                            UserFavorite.user_id.is_(None),
                        )
                        .order_by(User.user_id, Policy.policy_id)
                        .limit(1)
                    )
                ).one()
                user_id, policy_slug = pair

                created = await FavoriteService.add(
                    db,
                    user_id=user_id,
                    policy_slug=policy_slug,
                )
                assert created.policy_slug == policy_slug

                items, total = await FavoriteService.get_list(
                    db,
                    user_id=user_id,
                    page=1,
                    size=100,
                )
                assert total >= 1
                assert any(item.policy_id == created.policy_id for item in items)

                with pytest.raises(AppException) as duplicate:
                    await FavoriteService.add(
                        db,
                        user_id=user_id,
                        policy_slug=policy_slug,
                    )
                assert duplicate.value.status_code == status.HTTP_409_CONFLICT
                assert duplicate.value.code == ErrorCode.CONFLICT

                removed = await FavoriteService.remove(
                    db,
                    user_id=user_id,
                    policy_slug=policy_slug,
                )
                assert removed.removed is True

                favorite = (
                    await db.execute(
                        select(UserFavorite).where(
                            UserFavorite.user_id == user_id,
                            UserFavorite.policy_id == int(created.policy_id),
                        )
                    )
                ).scalar_one_or_none()
                assert favorite is None
            finally:
                await db.close()
                await transaction.rollback()

    asyncio.run(run_test())

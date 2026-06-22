from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppException, ErrorCode
from app.db.models.policy import Policy
from app.db.models.user_favorite import UserFavorite
from app.repositories.favorite_repository import FavoriteRepository
from app.schemas.favorite_schema import (
    FavoriteCreateResponse,
    FavoriteDeleteResponse,
    FavoritePolicyResponse,
)


class FavoriteService:
    @staticmethod
    async def add(
        db: AsyncSession,
        *,
        user_id: int,
        policy_slug: str,
    ) -> FavoriteCreateResponse:
        policy = await FavoriteService._get_policy_or_raise(db, policy_slug)
        existing = await FavoriteRepository.find_favorite(
            db,
            user_id,
            policy.policy_id,
        )
        if existing is not None:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.CONFLICT,
                message="Policy already saved as favorite",
            )

        try:
            favorite = await FavoriteRepository.save(
                db,
                UserFavorite(user_id=user_id, policy_id=policy.policy_id),
            )
        except IntegrityError as exc:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.CONFLICT,
                message="Policy already saved as favorite",
            ) from exc

        return FavoriteCreateResponse(
            **FavoriteService._policy_fields(policy),
            saved_at=favorite.saved_at,
        )

    @staticmethod
    async def remove(
        db: AsyncSession,
        *,
        user_id: int,
        policy_slug: str,
    ) -> FavoriteDeleteResponse:
        policy = await FavoriteService._get_policy_or_raise(db, policy_slug)
        favorite = await FavoriteRepository.find_favorite(
            db,
            user_id,
            policy.policy_id,
        )
        if favorite is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Favorite policy not found",
            )

        await FavoriteRepository.delete(db, favorite)
        return FavoriteDeleteResponse(
            policy_slug=policy.policy_code,
            removed=True,
        )

    @staticmethod
    async def get_list(
        db: AsyncSession,
        *,
        user_id: int,
        page: int,
        size: int,
    ) -> tuple[list[FavoritePolicyResponse], int]:
        rows, total = await FavoriteRepository.find_list(
            db,
            user_id=user_id,
            page=page,
            size=size,
        )
        return [
            FavoritePolicyResponse(
                policy_id=str(row["policy_id"]),
                policy_slug=row["policy_slug"],
                policy_name=row["policy_name"],
                category=row["category"],
                region=FavoriteService._resolve_region(
                    row["region_scope"],
                    row["region_code"],
                ),
                saved_at=row["saved_at"],
            )
            for row in rows
        ], total

    @staticmethod
    async def _get_policy_or_raise(
        db: AsyncSession,
        policy_slug: str,
    ) -> Policy:
        policy = await FavoriteRepository.find_policy_by_slug(
            db,
            policy_slug.strip(),
        )
        if policy is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message="Policy not found",
            )
        return policy

    @staticmethod
    def _policy_fields(policy: Policy) -> dict[str, Any]:
        return {
            "policy_id": str(policy.policy_id),
            "policy_slug": policy.policy_code,
            "policy_name": policy.policy_name,
            "category": policy.main_category,
            "region": FavoriteService._resolve_region(
                policy.region_scope,
                policy.region_code,
            ),
        }

    @staticmethod
    def _resolve_region(
        region_scope: str | None,
        region_code: str | None,
    ) -> str | None:
        if region_scope == "NATIONAL":
            return "national"
        return region_code

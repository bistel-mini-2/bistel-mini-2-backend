from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.profile import FamilyMember, UserProfile


class FamilyProfileRepository:
    @staticmethod
    async def find_profile_by_user_id(
        db: AsyncSession,
        user_id: int,
    ) -> UserProfile | None:
        result = await db.execute(
            select(UserProfile).where(UserProfile.user_id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def upsert_profile(
        db: AsyncSession,
        user_id: int,
        region_code: str,
        household_type: str | None,
        income_bracket: str | None,
        pregnancy_status: bool,
        profile_json: dict[str, Any],
    ) -> UserProfile:
        profile = await FamilyProfileRepository.find_profile_by_user_id(db, user_id)

        if profile is None:
            profile = UserProfile(
                user_id=user_id,
                region_code=region_code,
                household_type=household_type,
                income_bracket=income_bracket,
                pregnancy_status=pregnancy_status,
                profile_json=profile_json,
            )
            db.add(profile)
        else:
            profile.region_code = region_code
            profile.household_type = household_type
            profile.income_bracket = income_bracket
            profile.pregnancy_status = pregnancy_status
            profile.profile_json = profile_json

        await db.flush()
        await db.refresh(profile)
        return profile

    @staticmethod
    async def replace_family_members(
        db: AsyncSession,
        user_id: int,
        members: list[FamilyMember],
    ) -> None:
        await db.execute(delete(FamilyMember).where(FamilyMember.user_id == user_id))
        for member in members:
            db.add(member)
        await db.flush()

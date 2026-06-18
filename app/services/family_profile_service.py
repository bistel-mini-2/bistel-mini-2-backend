from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.policy_types import INCOME_LEVEL_TO_DB
from app.db.models.profile import FamilyMember, UserProfile
from app.db.models.user import User
from app.repositories.family_profile_repository import FamilyProfileRepository
from app.schemas.family_profile_schema import (
    FamilyChildAge,
    FamilyProfileData,
    FamilyProfileRequest,
    FamilyProfileResponse,
    FamilySpecialCondition,
    FamilyStage,
)


class FamilyProfileService:
    @staticmethod
    async def get_my_family_profile(
        db: AsyncSession,
        user: User,
    ) -> FamilyProfileResponse:
        profile = await FamilyProfileRepository.find_profile_by_user_id(
            db,
            user.user_id,
        )
        return FamilyProfileResponse(
            family_profile=FamilyProfileService._to_response_profile(profile)
        )

    @staticmethod
    async def save_my_family_profile(
        db: AsyncSession,
        user: User,
        request: FamilyProfileRequest,
    ) -> FamilyProfileResponse:
        profile_json = request.model_dump(mode="json", by_alias=True)
        profile = await FamilyProfileRepository.upsert_profile(
            db=db,
            user_id=user.user_id,
            region_code=request.region.value,
            household_type=FamilyProfileService._derive_household_type(
                request.special,
            ),
            income_bracket=INCOME_LEVEL_TO_DB[request.income.value],
            pregnancy_status=FamilyProfileService._is_pregnancy_profile(request),
            profile_json=profile_json,
        )
        await FamilyProfileRepository.replace_family_members(
            db=db,
            user_id=user.user_id,
            members=FamilyProfileService._build_family_members(user.user_id, request),
        )

        return FamilyProfileResponse(
            family_profile=FamilyProfileService._to_response_profile(profile)
        )

    @staticmethod
    def _to_response_profile(profile: UserProfile | None) -> FamilyProfileData | None:
        if profile is None or profile.profile_json is None:
            return None

        return FamilyProfileData.model_validate(
            {
                **profile.profile_json,
                "updated_at": profile.updated_at,
            }
        )

    @staticmethod
    def _derive_household_type(
        special: list[FamilySpecialCondition],
    ) -> str | None:
        if FamilySpecialCondition.SINGLE_PARENT in special:
            return "single_parent"
        if FamilySpecialCondition.MULTI_CHILD in special:
            return "multi_child"
        return None

    @staticmethod
    def _is_pregnancy_profile(request: FamilyProfileRequest) -> bool:
        return (
            request.stage == FamilyStage.PREGNANT
            or request.child_age == FamilyChildAge.PREBORN
        )

    @staticmethod
    def _build_family_members(
        user_id: int,
        request: FamilyProfileRequest,
    ) -> list[FamilyMember]:
        relation = (
            "expected_child"
            if FamilyProfileService._is_pregnancy_profile(request)
            else "child"
        )
        return [
            FamilyMember(
                user_id=user_id,
                relation=relation,
                birth_year=FamilyProfileService._derive_birth_year(
                    request.child_age,
                ),
                life_stage=request.stage.value,
                note=f"childAge={request.child_age.value}",
            )
        ]

    @staticmethod
    def _derive_birth_year(child_age: FamilyChildAge) -> int | None:
        current_year = datetime.now().year
        if child_age == FamilyChildAge.AGE_0:
            return current_year
        if child_age == FamilyChildAge.AGE_1:
            return current_year - 1
        return None

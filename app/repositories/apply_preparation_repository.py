from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.policy import Policy
from app.db.models.policy_checklist_template import PolicyChecklistTemplate
from app.db.models.policy_detail import PolicyDetail
from app.db.models.user_policy_checklist_item import UserPolicyChecklistItem
from app.db.models.user_policy_progress import UserPolicyProgress


class ApplyPreparationRepository:
    @staticmethod
    async def find_policy_by_code(db: AsyncSession, policy_code: str) -> Policy | None:
        result = await db.execute(select(Policy).where(Policy.policy_code == policy_code))
        return result.scalar_one_or_none()

    @staticmethod
    async def find_policy_detail(db: AsyncSession, policy_id: int) -> PolicyDetail | None:
        result = await db.execute(
            select(PolicyDetail).where(PolicyDetail.policy_id == policy_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_progress(
        db: AsyncSession, user_id: int, policy_id: int
    ) -> UserPolicyProgress | None:
        result = await db.execute(
            select(UserPolicyProgress).where(
                UserPolicyProgress.user_id == user_id,
                UserPolicyProgress.policy_id == policy_id,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_progress_by_id(
        db: AsyncSession, progress_id: int
    ) -> UserPolicyProgress | None:
        result = await db.execute(
            select(UserPolicyProgress).where(
                UserPolicyProgress.progress_id == progress_id
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_user_item_with_template(
        db: AsyncSession, progress_id: int, template_item_id: int
    ) -> tuple[UserPolicyChecklistItem, PolicyChecklistTemplate] | None:
        result = await db.execute(
            select(UserPolicyChecklistItem, PolicyChecklistTemplate)
            .join(
                PolicyChecklistTemplate,
                PolicyChecklistTemplate.template_item_id
                == UserPolicyChecklistItem.template_item_id,
            )
            .where(
                UserPolicyChecklistItem.progress_id == progress_id,
                UserPolicyChecklistItem.template_item_id == template_item_id,
            )
        )
        row = result.first()
        return (row[0], row[1]) if row else None

    @staticmethod
    async def save_progress(
        db: AsyncSession, progress: UserPolicyProgress
    ) -> UserPolicyProgress:
        db.add(progress)
        await db.flush()
        await db.refresh(progress)
        return progress

    @staticmethod
    async def find_active_templates(
        db: AsyncSession, policy_id: int
    ) -> list[PolicyChecklistTemplate]:
        result = await db.execute(
            select(PolicyChecklistTemplate)
            .where(
                PolicyChecklistTemplate.policy_id == policy_id,
                PolicyChecklistTemplate.is_active.is_(True),
            )
            .order_by(PolicyChecklistTemplate.display_order)
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_checklist_items(
        db: AsyncSession, progress_id: int
    ) -> list[UserPolicyChecklistItem]:
        result = await db.execute(
            select(UserPolicyChecklistItem).where(
                UserPolicyChecklistItem.progress_id == progress_id
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def bulk_create_checklist_items(
        db: AsyncSession, items: list[UserPolicyChecklistItem]
    ) -> list[UserPolicyChecklistItem]:
        db.add_all(items)
        await db.flush()
        return items

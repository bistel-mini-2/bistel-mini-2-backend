from datetime import datetime

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppException, ErrorCode
from app.db.models.policy import Policy
from app.db.models.policy_checklist_template import PolicyChecklistTemplate
from app.db.models.policy_detail import PolicyDetail
from app.db.models.user_policy_checklist_item import UserPolicyChecklistItem
from app.db.models.user_policy_progress import UserPolicyProgress
from app.repositories.apply_preparation_repository import ApplyPreparationRepository
from app.schemas.apply_schema import ApplyPreparationResponse, ChecklistItem


_APPLY_PERIOD_FALLBACK = "별도 확인 필요"


class ApplyPreparationService:
    @staticmethod
    async def create(
        db: AsyncSession, user_id: int, policy_slug: str
    ) -> ApplyPreparationResponse:
        policy = await _get_policy_or_raise(db, policy_slug)
        progress = await ApplyPreparationRepository.find_progress(
            db, user_id, policy.policy_id
        )

        if progress is None:
            progress = await ApplyPreparationRepository.save_progress(
                db,
                UserPolicyProgress(
                    user_id=user_id,
                    policy_id=policy.policy_id,
                    progress_status="PREPARING",
                    progress_percent=0,
                    started_at=datetime.now(),
                ),
            )
            templates = await ApplyPreparationRepository.find_active_templates(
                db, policy.policy_id
            )
            if templates:
                await ApplyPreparationRepository.bulk_create_checklist_items(
                    db,
                    [
                        UserPolicyChecklistItem(
                            progress_id=progress.progress_id,
                            template_item_id=template.template_item_id,
                            item_status="PENDING",
                        )
                        for template in templates
                    ],
                )

        return await _build_response(db, policy, progress)

    @staticmethod
    async def get(
        db: AsyncSession, user_id: int, policy_slug: str
    ) -> ApplyPreparationResponse:
        policy = await _get_policy_or_raise(db, policy_slug)
        progress = await ApplyPreparationRepository.find_progress(
            db, user_id, policy.policy_id
        )
        if progress is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Apply preparation not found",
            )
        return await _build_response(db, policy, progress)


async def _get_policy_or_raise(db: AsyncSession, policy_slug: str) -> Policy:
    policy = await ApplyPreparationRepository.find_policy_by_code(db, policy_slug)
    if policy is None:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.POLICY_NOT_FOUND,
            message="Policy not found",
        )
    return policy


async def _build_response(
    db: AsyncSession, policy: Policy, progress: UserPolicyProgress
) -> ApplyPreparationResponse:
    detail = await ApplyPreparationRepository.find_policy_detail(db, policy.policy_id)
    templates = await ApplyPreparationRepository.find_active_templates(db, policy.policy_id)
    items = await ApplyPreparationRepository.find_checklist_items(db, progress.progress_id)

    template_by_id = {template.template_item_id: template for template in templates}
    item_by_template_id = {item.template_item_id: item for item in items}

    checklist = [
        ChecklistItem(
            id=str(template.template_item_id),
            label=template.item_label,
            done=(
                item_by_template_id.get(template.template_item_id) is not None
                and item_by_template_id[template.template_item_id].item_status == "DONE"
            ),
        )
        for template in templates
    ]

    done_count = sum(1 for item in checklist if item.done)
    progress_percent = round(done_count * 100 / len(checklist)) if checklist else 0

    return ApplyPreparationResponse(
        apply_id=str(progress.progress_id),
        policy_id=policy.policy_code,
        how_to_apply=detail.application_method if detail else None,
        apply_period=_resolve_apply_period(detail, policy),
        contact=policy.contact,
        official_url=policy.official_url,
        checklist=checklist,
        caution=detail.caution if detail else None,
        progress_percent=progress_percent,
    )


def _resolve_apply_period(detail: PolicyDetail | None, policy: Policy) -> str:
    if detail and detail.application_period_text:
        return detail.application_period_text
    if policy.application_start_date and policy.application_end_date:
        return f"{policy.application_start_date} ~ {policy.application_end_date}"
    return _APPLY_PERIOD_FALLBACK

import asyncio
from unittest.mock import AsyncMock

from app.db.models.policy import Policy
from app.db.models.policy_checklist_template import PolicyChecklistTemplate
from app.db.models.policy_detail import PolicyDetail
from app.db.models.user_policy_checklist_item import UserPolicyChecklistItem
from app.db.models.user_policy_progress import UserPolicyProgress
from app.repositories.apply_preparation_repository import ApplyPreparationRepository
from app.services.apply_preparation_service import ApplyPreparationService


def _policy() -> Policy:
    return Policy(
        policy_id=1,
        policy_code="WLF00000001",
        policy_name="테스트 정책",
        contact="1234-5678",
        official_url="https://example.com",
    )


def _detail() -> PolicyDetail:
    return PolicyDetail(
        policy_id=1,
        application_method="온라인 신청",
        application_period_text="상시",
        caution="공식 사이트 확인 필요",
    )


def _template(template_item_id: int, label: str) -> PolicyChecklistTemplate:
    return PolicyChecklistTemplate(
        template_item_id=template_item_id,
        policy_id=1,
        item_code=f"ITEM_{template_item_id}",
        item_label=label,
        display_order=template_item_id,
        is_active=True,
    )


def test_get_returns_preview_without_creating_progress(monkeypatch) -> None:
    find_policy_by_code = AsyncMock(return_value=_policy())
    find_progress = AsyncMock(return_value=None)
    save_progress = AsyncMock()
    bulk_create_checklist_items = AsyncMock()
    find_policy_detail = AsyncMock(return_value=_detail())
    find_active_templates = AsyncMock(return_value=[_template(10, "신분증 준비")])
    find_checklist_items = AsyncMock()

    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_policy_by_code",
        find_policy_by_code,
    )
    monkeypatch.setattr(ApplyPreparationRepository, "find_progress", find_progress)
    monkeypatch.setattr(ApplyPreparationRepository, "save_progress", save_progress)
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "bulk_create_checklist_items",
        bulk_create_checklist_items,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_policy_detail",
        find_policy_detail,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_active_templates",
        find_active_templates,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_checklist_items",
        find_checklist_items,
    )

    response = asyncio.run(
        ApplyPreparationService.get(
            object(),  # type: ignore[arg-type]
            user_id=7,
            policy_slug="WLF00000001",
        )
    )

    assert response.apply_id is None
    assert response.saved is False
    assert response.policy_id == "WLF00000001"
    assert response.checklist[0].done is False
    assert response.progress_percent == 0
    save_progress.assert_not_awaited()
    bulk_create_checklist_items.assert_not_awaited()
    find_checklist_items.assert_not_awaited()


def test_create_backfills_missing_checklist_items(monkeypatch) -> None:
    progress = UserPolicyProgress(
        progress_id=22,
        user_id=7,
        policy_id=1,
        progress_status="PREPARING",
        progress_percent=0,
    )
    template_10 = _template(10, "신분증 준비")
    template_11 = _template(11, "통장 사본")
    existing_item = UserPolicyChecklistItem(
        progress_id=22,
        template_item_id=10,
        item_status="DONE",
    )
    backfilled_item = UserPolicyChecklistItem(
        progress_id=22,
        template_item_id=11,
        item_status="PENDING",
    )

    find_policy_by_code = AsyncMock(return_value=_policy())
    find_progress = AsyncMock(return_value=progress)
    save_progress = AsyncMock()
    find_policy_detail = AsyncMock(return_value=_detail())
    find_active_templates = AsyncMock(return_value=[template_10, template_11])
    find_checklist_items = AsyncMock(
        side_effect=[
            [existing_item],
            [existing_item, backfilled_item],
        ]
    )
    bulk_create_checklist_items = AsyncMock()

    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_policy_by_code",
        find_policy_by_code,
    )
    monkeypatch.setattr(ApplyPreparationRepository, "find_progress", find_progress)
    monkeypatch.setattr(ApplyPreparationRepository, "save_progress", save_progress)
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_policy_detail",
        find_policy_detail,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_active_templates",
        find_active_templates,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "find_checklist_items",
        find_checklist_items,
    )
    monkeypatch.setattr(
        ApplyPreparationRepository,
        "bulk_create_checklist_items",
        bulk_create_checklist_items,
    )

    response = asyncio.run(
        ApplyPreparationService.create(
            object(),  # type: ignore[arg-type]
            user_id=7,
            policy_slug="WLF00000001",
        )
    )

    assert response.apply_id == "22"
    assert response.saved is True
    assert [item.done for item in response.checklist] == [True, False]
    assert response.progress_percent == 50
    save_progress.assert_not_awaited()
    bulk_create_checklist_items.assert_awaited_once()
    created_items = bulk_create_checklist_items.await_args.args[1]
    assert [item.template_item_id for item in created_items] == [11]

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import AppException, ErrorCode
from app.repositories.compare_repository import CompareRepository
from app.services.compare_service import CompareService


def make_policy(
    *,
    policy_id: int,
    slug: str,
    name: str,
    category: str = "보육",
    tags: list[str] | None = None,
) -> dict:
    target_summary = f"{name} 대상 요약"
    source_text = f"{name} 조건 원문"
    return {
        "policy_id": policy_id,
        "slug": slug,
        "name": name,
        "category": category,
        "sub_category": "영유아",
        "agency": "보건복지부",
        "benefit_type": "현금",
        "application_status": "ONLINE_AVAILABLE",
        "application_start_date": None,
        "application_end_date": None,
        "region_scope": "NATIONAL",
        "region_code": None,
        "contact": "129",
        "official_url": "https://example.test",
        "condition_profile_id": policy_id + 100,
        "condition_profile_json": {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "type": "income",
                        "field": "median_income_percent",
                        "operator": "LTE",
                        "value": {"percent": 100 + policy_id},
                        "source_text": f"{name} 소득 조건",
                    },
                    {
                        "type": "stage",
                        "field": "stage",
                        "operator": "EQ",
                        "value": "infant",
                        "source_text": f"{name} 대상 조건",
                    },
                ],
            },
            "special_notes": [
                {
                    "source_text": f"{name} 추가 확인 조건",
                },
            ],
        },
        "condition_profile_target_summary": target_summary,
        "condition_profile_source_text": source_text,
        "condition_profile_confidence": 0.9,
        "condition_profile_review_required": False,
        "condition_profile_quality_flags": [],
        "condition_profile_source_fields": ["source_text"],
        "tags": tags or ["보육", "영유아"],
        "required_documents": ["신분증"],
    }


def test_compare_policies_returns_diff_and_related(monkeypatch) -> None:
    policy_a = make_policy(policy_id=1, slug="WLF00000001", name="A 정책")
    policy_b = make_policy(policy_id=2, slug="WLF00000002", name="B 정책")
    monkeypatch.setattr(
        CompareRepository,
        "find_policies_by_slugs",
        AsyncMock(return_value=[policy_a, policy_b]),
    )
    monkeypatch.setattr(
        CompareRepository,
        "find_related_policies",
        AsyncMock(
            return_value=[
                {"policy_id": 3, "slug": "WLF00000003", "name": "관련 정책"}
            ]
        ),
    )

    response = asyncio.run(
        CompareService().compare_policies(
            object(),  # type: ignore[arg-type]
            slug_a=" WLF00000001 ",
            slug_b="WLF00000002",
        )
    )

    assert response.policy_a.policy_id == "1"
    assert response.policy_a.slug == "WLF00000001"
    assert response.policy_a.summary["benefit"] == "현금"
    assert response.policy_a.summary["condition"] == "A 정책 대상 요약"
    assert response.policy_a.summary["source"] == "A 정책 조건 원문"
    assert response.policy_b.name == "B 정책"
    assert response.diff_table
    assert any(item.field == "지원 대상 요약" for item in response.diff_table)
    assert any(item.field == "소득 조건" for item in response.diff_table)
    assert response.selection_guide
    assert response.related_policies[0].slug == "WLF00000003"


def test_to_policy_summary_keeps_benefit_meaning() -> None:
    policy = make_policy(policy_id=1, slug="WLF00000001", name="A 정책")

    summary = CompareService._to_policy_summary(policy)

    assert summary.summary["benefit"] == "현금"
    assert summary.summary["condition"] == "A 정책 대상 요약"
    assert summary.summary["source"] == "A 정책 조건 원문"


def test_target_conditions_include_target_domain_aliases() -> None:
    condition_json = {
        "condition_tree": {
            "operator": "AND",
            "conditions": [
                {
                    "type": "target",
                    "field": "special_condition",
                    "source_text": "다문화가족 대상",
                },
                {
                    "type": "target_context",
                    "field": "eligible_household",
                    "source_text": "보호자가 돌봄 공백 상태인 가구",
                },
            ],
        },
    }

    row = {"condition_profile_json": condition_json}

    assert CompareService._field_value(row, "target_conditions") == [
        "다문화가족 대상",
        "보호자가 돌봄 공백 상태인 가구",
    ]


def test_income_conditions_include_benefit_status_aliases() -> None:
    condition_json = {
        "condition_tree": {
            "operator": "AND",
            "conditions": [
                {
                    "type": "income",
                    "field": "benefit_status",
                    "source_text": "기초생활보장 생계급여 수급자",
                },
                {
                    "type": "income_level",
                    "field": "income_bracket",
                    "source_text": "저소득층",
                },
            ],
        },
    }

    row = {"condition_profile_json": condition_json}

    assert CompareService._field_value(row, "income_conditions") == [
        "기초생활보장 생계급여 수급자",
        "저소득층",
    ]


def test_selection_guide_handles_missing_condition_profiles() -> None:
    policy_a = {
        "name": "A 정책",
        "condition_profile_target_summary": None,
        "condition_profile_source_text": None,
        "condition_profile_review_required": False,
    }
    policy_b = {
        "name": "B 정책",
        "condition_profile_target_summary": None,
        "condition_profile_source_text": None,
        "condition_profile_review_required": False,
    }

    assert CompareService._selection_guide(policy_a, policy_b) == (
        "두 정책 모두 정리된 조건 정보가 부족합니다. "
        "비교 결과는 공식 안내와 담당 기관 안내를 함께 확인하세요."
    )


def test_compare_policies_saves_history_when_user_exists(monkeypatch) -> None:
    policy_a = make_policy(policy_id=1, slug="WLF00000001", name="A 정책")
    policy_b = make_policy(policy_id=2, slug="WLF00000002", name="B 정책")
    save_history = AsyncMock(return_value=10)
    monkeypatch.setattr(
        CompareRepository,
        "find_policies_by_slugs",
        AsyncMock(return_value=[policy_a, policy_b]),
    )
    monkeypatch.setattr(
        CompareRepository,
        "find_related_policies",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(CompareRepository, "save_compare_history", save_history)
    fake_db = object()

    asyncio.run(
        CompareService().compare_policies(
            fake_db,  # type: ignore[arg-type]
            slug_a="WLF00000001",
            slug_b="WLF00000002",
            user_id=7,
        )
    )

    save_history.assert_awaited_once_with(
        fake_db, user_id=7, policy_a_id=1, policy_b_id=2
    )


def test_compare_policies_skips_history_when_user_missing(monkeypatch) -> None:
    policy_a = make_policy(policy_id=1, slug="WLF00000001", name="A 정책")
    policy_b = make_policy(policy_id=2, slug="WLF00000002", name="B 정책")
    save_history = AsyncMock(return_value=10)
    monkeypatch.setattr(
        CompareRepository,
        "find_policies_by_slugs",
        AsyncMock(return_value=[policy_a, policy_b]),
    )
    monkeypatch.setattr(
        CompareRepository,
        "find_related_policies",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(CompareRepository, "save_compare_history", save_history)

    asyncio.run(
        CompareService().compare_policies(
            object(),  # type: ignore[arg-type]
            slug_a="WLF00000001",
            slug_b="WLF00000002",
        )
    )

    save_history.assert_not_awaited()


def test_compare_policies_raises_404_when_slug_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        CompareRepository,
        "find_policies_by_slugs",
        AsyncMock(
            return_value=[
                make_policy(policy_id=1, slug="WLF00000001", name="A 정책")
            ]
        ),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            CompareService().compare_policies(
                object(),  # type: ignore[arg-type]
                slug_a="WLF00000001",
                slug_b="WLF00000002",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == ErrorCode.POLICY_NOT_FOUND


def test_get_compare_history_returns_items_and_total(monkeypatch) -> None:
    compared_at = datetime(2026, 6, 25, 10, 30, 0)
    monkeypatch.setattr(
        CompareRepository,
        "find_compare_history",
        AsyncMock(
            return_value=(
                [
                    {
                        "id": 3,
                        "policy_a_name": "A 정책",
                        "policy_b_name": "B 정책",
                        "policy_a_slug": "WLF00000001",
                        "policy_b_slug": "WLF00000002",
                        "compared_at": compared_at,
                    }
                ],
                1,
            )
        ),
    )

    items, total = asyncio.run(
        CompareService().get_compare_history(
            object(),  # type: ignore[arg-type]
            user_id=7,
            page=1,
            size=20,
        )
    )

    assert total == 1
    assert items[0].id == "3"
    assert items[0].policy_a_slug == "WLF00000001"
    assert items[0].compared_at == compared_at


def test_delete_compare_history_returns_deleted_count(monkeypatch) -> None:
    soft_delete = AsyncMock(return_value=True)
    monkeypatch.setattr(
        CompareRepository,
        "soft_delete_compare_history",
        soft_delete,
    )
    fake_db = object()

    deleted_count = asyncio.run(
        CompareService().delete_compare_history(
            fake_db,  # type: ignore[arg-type]
            user_id=7,
            history_id=3,
        )
    )

    assert deleted_count == 1
    soft_delete.assert_awaited_once_with(fake_db, user_id=7, history_id=3)


def test_delete_compare_history_raises_404_when_not_found(monkeypatch) -> None:
    monkeypatch.setattr(
        CompareRepository,
        "soft_delete_compare_history",
        AsyncMock(return_value=False),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            CompareService().delete_compare_history(
                object(),  # type: ignore[arg-type]
                user_id=7,
                history_id=999,
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == ErrorCode.NOT_FOUND


def test_delete_all_compare_history_returns_deleted_count(monkeypatch) -> None:
    soft_delete_all = AsyncMock(return_value=4)
    monkeypatch.setattr(
        CompareRepository,
        "soft_delete_all_compare_history",
        soft_delete_all,
    )
    fake_db = object()

    deleted_count = asyncio.run(
        CompareService().delete_all_compare_history(
            fake_db,  # type: ignore[arg-type]
            user_id=7,
        )
    )

    assert deleted_count == 4
    soft_delete_all.assert_awaited_once_with(fake_db, user_id=7)

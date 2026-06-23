import asyncio
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
        "easy_summary": f"{name} 요약",
        "target_description": f"{name} 대상",
        "benefit_description": f"{name} 혜택",
        "application_method": f"{name} 신청 방법",
        "application_period_text": "상시",
        "caution": f"{name} 유의사항",
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

    assert response.policy_a.policy_id == "WLF00000001"
    assert response.policy_b.name == "B 정책"
    assert response.diff_table
    assert any(item.field == "지원 대상" for item in response.diff_table)
    assert response.selection_guide
    assert response.related_policies[0].slug == "WLF00000003"


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

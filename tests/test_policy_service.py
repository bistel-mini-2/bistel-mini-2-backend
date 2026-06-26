import asyncio
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import AppException, ErrorCode
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import PolicySort
from app.services.policy_service import PolicyService


def test_get_policy_list_normalizes_documented_filters(monkeypatch) -> None:
    find_policy_list = AsyncMock(return_value=([], 0))
    db = object()
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_list",
        find_policy_list,
    )

    items, total = asyncio.run(
        PolicyService().get_policy_list(
            db,  # type: ignore[arg-type]
            query=r"  100%_지원 정책  ",
            category="  생활지원  ",
            tags=[" 임신·출산, 영유아", "영유아, "],
            region_code=" national ",
            stage=" pregnant ",
            sort=PolicySort.RELEVANCE,
            page=2,
            size=10,
        ),
    )

    assert items == []
    assert total == 0
    find_policy_list.assert_awaited_once_with(
        db,
        query=r"100%_지원 정책",
        query_pattern=r"%100\%\_지원 정책%",
        category="생활지원",
        tags=["임신·출산", "영유아"],
        region_code="national",
        stage_tags=["임신 · 출산", "임신·출산"],
        stage="pregnant",
        sort=PolicySort.RELEVANCE,
        page=2,
        size=10,
    )


def test_to_response_adds_issue_29_compatible_fields() -> None:
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "테스트 정책",
        "category": "생활지원",
        "sub_category": None,
        "tags": ["영유아"],
        "summary": None,
        "benefit_summary": None,
        "agency": None,
        "benefit_type": None,
        "application_status": None,
        "application_start_date": None,
        "application_end_date": None,
        "application_period_text": None,
        "region_scope": "NATIONAL",
        "region_code": None,
        "official_url": None,
    }

    response = PolicyService._to_response(row)

    assert response.target_stage == ["newborn", "infant"]
    assert response.deadline is None
    assert response.region == "national"


def test_to_response_prefers_condition_profile_target_stage() -> None:
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "test policy",
        "category": "support",
        "sub_category": None,
        "tags": [],
        "summary": None,
        "benefit_summary": None,
        "agency": None,
        "benefit_type": None,
        "application_status": None,
        "application_start_date": None,
        "application_end_date": None,
        "application_period_text": None,
        "region_scope": "NATIONAL",
        "region_code": None,
        "official_url": None,
        "condition_profile_json": {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "stage",
                        "operator": "IN",
                        "value": ["pregnant", "child"],
                    }
                ],
            }
        },
    }

    response = PolicyService._to_response(row)

    assert response.target_stage == ["pregnant", "child"]


def test_get_policy_detail_returns_detail_fields(monkeypatch) -> None:
    db = object()
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "테스트 정책",
        "category": "생활지원",
        "sub_category": None,
        "tags": ["영유아"],
        "summary": "쉬운 요약",
        "benefit_summary": "지원 내용",
        "agency": "보건복지부",
        "benefit_type": "현금",
        "application_status": "AVAILABLE",
        "application_start_date": None,
        "application_end_date": None,
        "application_period_text": "상시 신청",
        "region_scope": "NATIONAL",
        "region_code": None,
        "official_url": "https://example.com",
        "contact": "129",
        "easy_summary": "쉬운 요약",
        "target_description": "지원 대상",
        "benefit_description": "지원 내용",
        "application_method": "온라인 신청",
        "caution": "주의 사항",
    }
    find_policy_detail = AsyncMock(return_value=row)
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        find_policy_detail,
    )

    response = asyncio.run(
        PolicyService().get_policy_detail(
            db,  # type: ignore[arg-type]
            policy_slug=" WLF00000024 ",
        )
    )

    assert response.slug == "WLF00000024"
    assert response.target_description == "지원 대상"
    assert response.contact == "129"
    find_policy_detail.assert_awaited_once_with(
        db,
        policy_slug="WLF00000024",
    )


def test_to_detail_response_includes_condition_profile() -> None:
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "test policy",
        "category": "support",
        "sub_category": None,
        "tags": [],
        "summary": "summary",
        "benefit_summary": "benefit",
        "agency": "agency",
        "benefit_type": "cash",
        "application_status": "AVAILABLE",
        "application_start_date": None,
        "application_end_date": None,
        "application_period_text": "always",
        "region_scope": "NATIONAL",
        "region_code": None,
        "official_url": "https://example.com",
        "contact": "129",
        "easy_summary": "easy",
        "target_description": "target",
        "benefit_description": "benefit",
        "application_method": "online",
        "caution": None,
        "condition_profile_json": {
            "condition_tree": {
                "field": "stage",
                "operator": "EQ",
                "value": "pregnant",
            }
        },
        "condition_profile_target_summary": "profile target",
        "condition_profile_confidence": 0.9,
        "condition_profile_review_required": False,
        "condition_profile_quality_flags": [],
        "condition_profile_source_text": "source text",
        "condition_profile_source_fields": ["raw_selection_criteria"],
    }

    response = PolicyService._to_detail_response(row)

    assert response.condition_profile is not None
    assert response.condition_profile.target_summary == "profile target"
    assert response.condition_profile.condition_json["condition_tree"]["value"] == (
        "pregnant"
    )
    assert response.condition_profile.source_text == "source text"


def test_get_policy_detail_rejects_unknown_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            PolicyService().get_policy_detail(
                object(),  # type: ignore[arg-type]
                policy_slug="UNKNOWN",
            )
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == ErrorCode.POLICY_NOT_FOUND

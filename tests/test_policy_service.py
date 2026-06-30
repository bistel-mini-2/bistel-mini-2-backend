import asyncio
from unittest.mock import AsyncMock

import pytest

from app.common.exceptions import AppException, ErrorCode
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import PolicySearchScope, PolicySort
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
        search_scope=PolicySearchScope.ALL,
    ),
)

    assert items == []
    assert total == 0
    find_policy_list.assert_awaited_once_with(
        db,
        query=r"100%_지원 정책",
        query_pattern=r"%100\%\_지원 정책%",
        detail_query_pattern=None,
        category="생활지원",
        tags=["임신·출산", "영유아"],
        region_code="national",
        stage_tags=["임신 · 출산", "임신·출산"],
        stage="pregnant",
        search_scope=PolicySearchScope.ALL,
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
    assert response.target_stage_display == ["신생아", "영유아"]
    assert response.display_age == "신생아, 영유아"
    assert response.region == "national"
    assert response.region_display == "전국"


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
    assert response.life_stage_display == "임신·출산, 아동"


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
    monkeypatch.setattr(
        PolicyRepository,
        "find_related_policies",
        AsyncMock(return_value=[]),
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
    assert response.conditions == "profile target"
    assert response.application_guide is not None
    assert response.application_guide.summary == "online"
    assert response.application_status_display == "신청 가능"


def test_get_policy_detail_includes_related_policies(monkeypatch) -> None:
    db = object()
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "test policy",
        "category": "support",
        "sub_category": None,
        "tags": ["pregnancy"],
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
    }
    related_row = {
        **row,
        "policy_id": 2,
        "slug": "WLF00000025",
        "name": "related policy",
        "related_score": 11,
        "related_match_category": True,
        "related_match_stage": True,
        "related_match_region": True,
        "related_match_tag": True,
    }
    find_policy_detail = AsyncMock(return_value=row)
    find_related_policies = AsyncMock(return_value=[related_row])
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        find_policy_detail,
    )
    monkeypatch.setattr(
        PolicyRepository,
        "find_related_policies",
        find_related_policies,
    )

    response = asyncio.run(
        PolicyService().get_policy_detail(
            db,  # type: ignore[arg-type]
            policy_slug="WLF00000024",
        )
    )

    assert response.benefit == "benefit"
    assert response.conditions == "target"
    assert response.how_to_apply == "online"
    assert response.related_policies[0].slug == "WLF00000025"
    assert response.related_policies[0].related_reason == (
        "같은 분야, 같은 생애 단계, 같은 지역, 유사 태그 기준으로 함께 확인할 만한 정책입니다."
    )
    find_related_policies.assert_awaited_once_with(
        db,
        excluded_policy_ids=[1],
        category="support",
        region_scope="NATIONAL",
        region_code=None,
        target_stages=["pregnant"],
        tags=["pregnancy"],
        limit=3,
    )


def test_to_response_marks_all_age_display() -> None:
    row = {
        "policy_id": 1,
        "slug": "WLF00000024",
        "name": "전 연령 정책",
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
        "target_description": "전 연령 누구나 신청할 수 있습니다.",
        "benefit_description": "돌봄 서비스를 제공합니다.",
        "condition_profile_json": {"condition_tree": {}},
    }

    response = PolicyService._to_response(row)

    assert response.all_age is True
    assert response.display_age == "전 연령 대상"
    assert response.life_stage_display == "모든 생애 단계 대상"


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

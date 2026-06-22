import asyncio
from unittest.mock import AsyncMock

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

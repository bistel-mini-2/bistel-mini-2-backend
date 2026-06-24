from typing import Any

import pytest

from app.services.policy_condition_profile_service import (
    PolicyConditionProfileService,
    _PolicyConditionExtractionModel,
)


@pytest.mark.asyncio
async def test_condition_profile_preserves_income_and_target_or_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: dict[str, Any] = {}

    class FakeRepository:
        @staticmethod
        async def ensure_schema(conn) -> None:
            return None

        @staticmethod
        async def find_profile_targets(
            conn,
            limit: int,
            overwrite: bool = False,
        ) -> list[dict[str, Any]]:
            return [
                {
                    "policy_id": 236,
                    "policy_code": "WLF00000236",
                    "policy_name": "농식품바우처",
                    "main_category": "생활지원",
                    "sub_category": "임신·출산",
                    "benefit_type": "이용권",
                    "life_array": "임신 · 출산, 영유아, 아동, 청소년",
                    "target_individual_array": "저소득",
                    "interest_theme_array": None,
                    "raw_target_detail": (
                        "생계급여 수급가구 중 임산부 또는 34세 이하 인지를 "
                        "포함하고 있는 가구"
                    ),
                    "raw_selection_criteria": "기준중위소득 32% 이하",
                    "target_description": None,
                    "easy_summary": None,
                    "raw_outline": None,
                    "raw_benefit_content": None,
                    "benefit_description": None,
                    "application_period_text": None,
                    "caution": (
                        "보장시설 수급가구 수급자 및 보건복지부 영양플러스 "
                        "사업 이용자는 제외"
                    ),
                }
            ]

        @staticmethod
        async def upsert_profile(conn, **kwargs) -> int:
            saved.update(kwargs)
            return 9001

    class FakeTransaction:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakeConnection:
        def transaction(self) -> FakeTransaction:
            return FakeTransaction()

    async def fake_extractor(
        target: dict[str, Any],
        source_text: str,
        source_fields: list[str],
    ) -> _PolicyConditionExtractionModel:
        return _PolicyConditionExtractionModel(
            target_summary=(
                "생계급여 수급가구 중 임산부 또는 34세 이하 가구원이 있는 가구"
            ),
            condition_tree={
                "operator": "AND",
                "conditions": [
                    {
                        "group_key": "income",
                        "operator": "AND",
                        "conditions": [
                            {
                                "type": "income",
                                "field": "income_status",
                                "operator": "IN",
                                "value": ["basic_livelihood_recipient"],
                                "source_text": "생계급여 수급가구",
                                "confidence": 0.95,
                            },
                            {
                                "type": "income",
                                "field": "median_income",
                                "operator": "LTE",
                                "value": {"percent": 32},
                                "source_text": "기준중위소득 32% 이하",
                                "confidence": 0.95,
                            },
                        ],
                    },
                    {
                        "group_key": "target",
                        "operator": "OR",
                        "conditions": [
                            {
                                "type": "stage",
                                "field": "stage",
                                "operator": "IN",
                                "value": ["pregnant"],
                                "source_text": "임산부",
                                "confidence": 0.95,
                            },
                            {
                                "type": "age",
                                "field": "household_member_age",
                                "operator": "LTE",
                                "value": {"years": 34},
                                "source_text": "34세 이하",
                                "confidence": 0.9,
                            },
                        ],
                    },
                ],
            },
            exclusions=[
                {
                    "type": "program_overlap",
                    "value": "보건복지부 영양플러스 사업 이용자",
                    "source_text": "보건복지부 영양플러스 사업 이용자는 제외",
                    "confidence": 0.85,
                }
            ],
            confidence=0.93,
            review_required=False,
            quality_flags=[],
        )

    service = PolicyConditionProfileService(
        repository=FakeRepository,
        extractor=fake_extractor,
    )

    from app.services import policy_condition_profile_service as service_module

    class FakePoolConnection:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakePool:
        def connection(self) -> FakePoolConnection:
            return FakePoolConnection()

    monkeypatch.setattr(service_module, "psycopg_pool", FakePool())
    response = await service.ingest_condition_profiles(limit=1)

    assert response.completed_count == 1
    assert saved["policy_id"] == 236
    assert saved["review_required"] is False
    assert "raw_target_detail" in saved["source_fields"]

    condition_tree = saved["condition_json"]["condition_tree"]
    assert condition_tree["operator"] == "AND"
    assert condition_tree["conditions"][0]["operator"] == "AND"
    assert condition_tree["conditions"][1]["operator"] == "OR"
    assert saved["condition_json"]["exclusions"][0]["type"] == "program_overlap"


def test_system_prompt_prioritizes_target_text_over_openapi_category() -> None:
    prompt = PolicyConditionProfileService()._system_prompt()

    assert "OpenAPI의 category, sub_category, lifeArray, trgterIndvdlArray는 참고 정보" in prompt
    assert "지원대상, 선정기준, target_description 원문을 우선" in prompt
    assert "지원대상/선정기준 원문을 기준으로 condition_tree" in prompt

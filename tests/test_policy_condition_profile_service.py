import asyncio
from typing import Any

import pytest

from app.services import policy_condition_profile_service as service_module
from app.services.policy_condition_profile_service import (
    QUALITY_EMPTY_CONDITION_TREE,
    QUALITY_VALIDATION_ADJUSTED,
    QUALITY_VALIDATION_ERROR,
    REASON_ASSET_NOT_REGION,
    REASON_BIRTH_EVENT_NOT_STAGE,
    REASON_DETAILED_DISABILITY,
    REASON_FIELD_NOT_SUPPORTED,
    STANDARD_OPERATORS,
    STRENGTH_FOLLOW_UP,
    STRENGTH_HARD,
    STRENGTH_SOFT,
    PolicyConditionProfileService,
    _PolicyConditionExtractionModel,
)


def _make_target(**overrides: Any) -> dict[str, Any]:
    target: dict[str, Any] = {
        "policy_id": 1,
        "policy_code": "WLF00000001",
        "policy_name": "테스트정책",
        "main_category": None,
        "sub_category": None,
        "benefit_type": None,
        "life_array": None,
        "target_individual_array": None,
        "interest_theme_array": None,
        "raw_target_detail": "지원대상 원문",
        "raw_selection_criteria": None,
        "target_description": None,
        "easy_summary": None,
        "raw_outline": None,
        "raw_benefit_content": None,
        "benefit_description": None,
        "application_period_text": None,
        "caution": None,
    }
    target.update(overrides)
    return target


def _run_ingest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: dict[str, Any],
    extraction: _PolicyConditionExtractionModel,
) -> dict[str, Any]:
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
            return [target]

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

    class FakePoolConnection:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    class FakePool:
        def connection(self) -> FakePoolConnection:
            return FakePoolConnection()

    async def fake_extractor(
        _target: dict[str, Any],
        source_text: str,
        source_fields: list[str],
    ) -> _PolicyConditionExtractionModel:
        return extraction

    service = PolicyConditionProfileService(
        repository=FakeRepository,
        extractor=fake_extractor,
    )
    monkeypatch.setattr(service_module, "psycopg_pool", FakePool())
    asyncio.run(service.ingest_condition_profiles(limit=1))
    return saved


def test_condition_profile_preserves_income_and_target_or_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _make_target(
        policy_id=236,
        policy_code="WLF00000236",
        policy_name="농식품바우처",
        main_category="생활지원",
        sub_category="임신·출산",
        benefit_type="이용권",
        life_array="임신 · 출산, 영유아, 아동, 청소년",
        target_individual_array="저소득",
        raw_target_detail=(
            "생계급여 수급가구 중 임산부 또는 34세 이하 인지를 포함하고 있는 가구"
        ),
        raw_selection_criteria="기준중위소득 32% 이하",
        caution="보건복지부 영양플러스 사업 이용자는 제외",
    )
    extraction = _PolicyConditionExtractionModel(
        target_summary="생계급여 수급가구 중 임산부 또는 34세 이하 가구원이 있는 가구",
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

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)

    assert saved["policy_id"] == 236
    assert saved["review_required"] is False
    assert "raw_target_detail" in saved["source_fields"]

    condition_tree = saved["condition_json"]["condition_tree"]
    assert condition_tree["operator"] == "AND"
    assert condition_tree["conditions"][0]["operator"] == "AND"
    assert condition_tree["conditions"][1]["operator"] == "OR"
    assert saved["condition_json"]["exclusions"][0]["type"] == "program_overlap"


def _all_leaves(node: Any, acc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        if isinstance(node.get("conditions"), list):
            for child in node["conditions"]:
                _all_leaves(child, acc)
        elif node.get("field"):
            acc.append(node)
    return acc


def _collect_leaf_fields(node: Any) -> list[Any]:
    return [leaf.get("field") for leaf in _all_leaves(node, [])]


def _collect_leaf_operators(node: Any) -> list[Any]:
    return [leaf.get("operator") for leaf in _all_leaves(node, [])]


def _leaf_by_field(node: Any, field: str) -> dict[str, Any] | None:
    for leaf in _all_leaves(node, []):
        if leaf.get("field") == field:
            return leaf
    return None


def _find_group(node: Any, operator: str) -> dict[str, Any] | None:
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("conditions"), list):
        if str(node.get("operator")).upper() == operator:
            return node
        for child in node["conditions"]:
            found = _find_group(child, operator)
            if found is not None:
                return found
    return None


def test_236_root_and_hard_stage_and_rebucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """236. root AND, income/stage hard, 중복 제거, 외국인/산출제외 재배치."""
    target = _make_target(
        policy_id=236,
        raw_target_detail=(
            "생계급여 수급가구 중 임산부, 영유아, 아동, 청년이 포함된 가구"
        ),
    )
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {
                    "field": "income_status",
                    "operator": "IN",
                    "value": ["basic_livelihood_recipient"],
                    "source_text": "생계급여 수급가구",
                },
                {
                    "operator": "OR",
                    "group_key": "target",
                    "conditions": [
                        {"field": "stage", "operator": "IN", "value": ["pregnant"]},
                        {
                            "field": "stage",
                            "operator": "IN",
                            "value": ["newborn", "infant"],
                        },
                        {"field": "stage", "operator": "IN", "value": ["child"]},
                        {"field": "stage", "operator": "IN", "value": ["youth"]},
                        # 의미 중복 → dedupe로 제거되어야 함
                        {"field": "stage", "operator": "IN", "value": ["pregnant"]},
                    ],
                },
                {
                    "field": "foreigners",
                    "operator": "EXISTS",
                    "source_text": "외국인과 난민도 지원 가능",
                },
            ],
        },
        exclusions=[
            {"source_text": "외국인과 난민도 자격 기준에 맞으면 지원 가능"},
            {"source_text": "보장시설 수급가구 수급자는 가구원 수 산출에서 제외"},
        ],
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    assert tree["operator"] == "AND"
    income_leaf = _leaf_by_field(tree, "income_status")
    assert income_leaf is not None and income_leaf["matching_strength"] == STRENGTH_HARD

    or_group = _find_group(tree, "OR")
    assert or_group is not None and len(or_group["conditions"]) == 4

    assert "foreigners" not in _collect_leaf_fields(tree)
    assert {i.get("field") for i in condition_json["unsupported_conditions"]} == {
        "nationality_status"
    }

    exclusion_texts = " ".join(
        str(i.get("source_text", "")) for i in condition_json["exclusions"]
    )
    assert "지원 가능" not in exclusion_texts
    assert "산출에서 제외" not in exclusion_texts
    special_texts = " ".join(
        str(i.get("source_text", "")) for i in condition_json["special_notes"]
    )
    assert "산출에서 제외" in special_texts
    assert saved["review_required"] is True


def test_237_disability_unsupported_employment_soft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """237. 상세장애는 unsupported, 근로자는 soft로 tree 유지."""
    target = _make_target(policy_id=237, raw_target_detail="발달장애 근로자")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {
                    "field": "disability_type",
                    "operator": "EQ",
                    "value": "developmental_disability",
                    "source_text": "발달장애",
                },
                {
                    "field": "employment_status",
                    "operator": "EQ",
                    "value": "employed",
                    "source_text": "근로자",
                },
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    employment_leaf = _leaf_by_field(tree, "employment_status")
    assert employment_leaf is not None
    assert employment_leaf["matching_strength"] == STRENGTH_SOFT
    assert "disability_type" not in _collect_leaf_fields(tree)

    detailed = [
        i
        for i in condition_json["unsupported_conditions"]
        if i.get("reason") == REASON_DETAILED_DISABILITY
    ]
    assert detailed and detailed[0]["source_text"] == "발달장애"
    assert saved["review_required"] is True


def test_238_special_conditions_hard_farmer_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """238. 다문화/장애인/청소년은 hard, 농업인은 unsupported."""
    target = _make_target(
        policy_id=238,
        raw_target_detail="다문화가족, 북한이탈주민, 장애인, 농업인, 청소년",
    )
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "OR",
            "conditions": [
                {
                    "field": "special_condition",
                    "operator": "IN",
                    "value": ["multicultural_or_defector"],
                    "source_text": "다문화가족, 북한이탈주민",
                },
                {
                    "field": "special_condition",
                    "operator": "IN",
                    "value": ["disabled_household"],
                    "source_text": "장애인",
                },
                {"field": "stage", "operator": "IN", "value": ["youth"]},
                {
                    "field": "farmer_status",
                    "operator": "EXISTS",
                    "source_text": "농업인",
                },
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    fields = _collect_leaf_fields(tree)
    assert fields.count("special_condition") == 2
    assert "stage" in fields
    assert "farmer_status" not in fields
    for leaf in _all_leaves(tree, []):
        assert leaf["matching_strength"] == STRENGTH_HARD

    assert any(
        i.get("field") == "farmer_status"
        and i.get("reason") == REASON_FIELD_NOT_SUPPORTED
        for i in condition_json["unsupported_conditions"]
    )
    assert saved["review_required"] is True


def test_239_identity_conditions_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """239. 외국인/귀화/체류 조건은 unsupported, tree 비움."""
    target = _make_target(policy_id=239, raw_target_detail="합법 체류 외국인, 귀화자")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {
                    "field": "nationality_status",
                    "operator": "EXISTS",
                    "source_text": "합법 체류 외국인",
                }
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert condition_json["condition_tree"] == {}
    assert {i.get("field") for i in condition_json["unsupported_conditions"]} == {
        "nationality_status"
    }
    assert saved["review_required"] is True


def test_241_debt_bankruptcy_follow_up_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """241. 채무/파산 조건은 follow_up으로 tree 유지."""
    target = _make_target(policy_id=241, raw_target_detail="감당할 수 없는 빚")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "OR",
            "conditions": [
                {
                    "field": "debt_status",
                    "operator": "EXISTS",
                    "source_text": "감당할 수 없는 빚",
                },
                {
                    "field": "bankruptcy_status",
                    "operator": "EXISTS",
                    "source_text": "개인회생, 개인파산 및 면책 제도",
                },
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    for field in ("debt_status", "bankruptcy_status"):
        leaf = _leaf_by_field(tree, field)
        assert leaf is not None
        assert leaf["matching_strength"] == STRENGTH_FOLLOW_UP

    assert condition_json["follow_up_required"] is True
    assert saved["review_required"] is True


def test_242_legal_consultation_follow_up_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """242. 법률상담 필요는 follow_up으로 유지(단일 leaf tree)."""
    target = _make_target(policy_id=242, raw_target_detail="법률 상담이 필요한 국민")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "field": "legal_consultation_need",
            "operator": "EXISTS",
            "source_text": "법률 상담이 필요한 국민",
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    assert tree["field"] == "legal_consultation_need"
    assert tree["matching_strength"] == STRENGTH_FOLLOW_UP
    assert condition_json["follow_up_required"] is True
    assert saved["review_required"] is True


def test_243_environmental_follow_up_and_explicit_exclusion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """243. 환경오염 피해는 follow_up, 명시적 제외는 exclusions."""
    target = _make_target(policy_id=243, raw_target_detail="환경오염 피해")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "field": "environmental_damage_type",
            "operator": "EXISTS",
            "source_text": "환경오염 피해",
        },
        ignored_conditions=[
            {
                "source_text": (
                    "해당사업자가 받은 피해와 해당 사업자의 종업원이 업무상 받은 "
                    "피해는 제외"
                )
            }
        ],
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    assert tree["field"] == "environmental_damage_type"
    assert tree["matching_strength"] == STRENGTH_FOLLOW_UP
    exclusion_texts = " ".join(
        str(i.get("source_text", "")) for i in condition_json["exclusions"]
    )
    assert "업무상 받은 피해는 제외" in exclusion_texts
    assert saved["review_required"] is True


def test_244_income_hard_accident_follow_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """244. 소득은 hard, 자동차사고 피해는 follow_up."""
    target = _make_target(
        policy_id=244,
        raw_target_detail="자동차 사고 피해자 중 수급자 또는 차상위계층",
    )
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {
                    "field": "income_status",
                    "operator": "IN",
                    "value": ["basic_livelihood_recipient", "near_poverty_class"],
                    "source_text": "수급자 또는 차상위계층",
                },
                {
                    "field": "accident_victim_status",
                    "operator": "EXISTS",
                    "source_text": "자동차 사고 피해자",
                },
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]
    tree = condition_json["condition_tree"]

    income_leaf = _leaf_by_field(tree, "income_status")
    assert income_leaf is not None
    assert income_leaf["matching_strength"] == STRENGTH_HARD
    assert set(income_leaf["value"]) == {
        "basic_livelihood_recipient",
        "near_poverty_class",
    }
    accident_leaf = _leaf_by_field(tree, "accident_victim_status")
    assert accident_leaf is not None
    assert accident_leaf["matching_strength"] == STRENGTH_FOLLOW_UP
    assert condition_json["follow_up_required"] is True
    assert saved["review_required"] is True


def test_operator_enum_normalized_on_hard_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _make_target(raw_target_detail="operator 정규화")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {
                    "field": "income_status",
                    "operator": "EQUAL",
                    "value": ["basic_livelihood_recipient"],
                },
                {"field": "stage", "operator": "EQUALS", "value": ["youth"]},
                {"field": "pregnancy_status", "operator": "exists"},
                {"field": "age", "operator": "unknown"},
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    tree = saved["condition_json"]["condition_tree"]

    operators = _collect_leaf_operators(tree)
    assert all(op in STANDARD_OPERATORS for op in operators)
    assert operators == ["EQ", "EQ", "EXISTS", "UNKNOWN"]
    for leaf in _all_leaves(tree, []):
        assert leaf["matching_strength"] == STRENGTH_HARD


def test_excludes_operator_stripped_and_lt_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _make_target(policy_id=240, raw_target_detail="2세 미만")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "AND",
            "conditions": [
                {"field": "child_age", "operator": "LT", "value": 2, "source_text": "2세 미만"},
            ],
        },
        exclusions=[
            {
                "field": "insurance_status",
                "operator": "EXCLUDES",
                "source_text": "제외 대상 : 급여정지자",
            },
        ],
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    for item in condition_json["exclusions"]:
        assert str(item.get("operator", "")).upper() != "EXCLUDES"
    child_leaf = _leaf_by_field(condition_json["condition_tree"], "child_age")
    assert child_leaf is not None and child_leaf["operator"] == "LT"


def test_duplicate_leaves_are_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    target = _make_target(raw_target_detail="중복 제거")
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "OR",
            "conditions": [
                {"field": "stage", "operator": "IN", "value": ["youth"]},
                {"field": "stage", "operator": "IN", "value": ["youth"]},
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    tree = saved["condition_json"]["condition_tree"]
    assert len(tree["conditions"]) == 1


def test_system_prompt_states_matching_strength_rules() -> None:
    prompt = PolicyConditionProfileService()._system_prompt()

    assert "matching_strength" in prompt
    assert "hard" in prompt
    assert "soft" in prompt
    assert "follow_up" in prompt
    assert "농업인" in prompt
    assert "법률상담" in prompt


def test_system_prompt_prioritizes_target_text_over_openapi_category() -> None:
    prompt = PolicyConditionProfileService()._system_prompt()

    assert "OpenAPI의 category, sub_category, lifeArray, trgterIndvdlArray는 참고 정보" in prompt
    assert "지원대상, 선정기준, target_description 원문을 우선" in prompt
    assert "지원대상/선정기준 원문을 기준으로 condition_tree" in prompt


def test_system_prompt_states_operator_and_and_or_rules() -> None:
    prompt = PolicyConditionProfileService()._system_prompt()

    for operator in ("EXISTS", "EQ", "IN", "LTE", "GTE", "LT", "GT", "UNKNOWN"):
        assert operator in prompt
    assert "수급자 또는 차상위계층" in prompt
    assert "산출에서 제외" in prompt
    assert "지원 가능" in prompt
    assert '"미만"=LT' in prompt
    assert "선행 필수 조건" in prompt


# --- validation layer 테스트 ---


def _unsupported_fields(condition_json: dict[str, Any]) -> set[Any]:
    return {item.get("field") for item in condition_json["unsupported_conditions"]}


def _single_leaf_tree(leaf: dict[str, Any]) -> dict[str, Any]:
    return {"operator": "AND", "conditions": [leaf]}


def test_validation_256_birth_events_removed_from_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """256. 출산/유산/사산은 stage가 아니라 unsupported(birth_event)로 이동."""
    target = _make_target(policy_id=256)
    extraction = _PolicyConditionExtractionModel(
        condition_tree={
            "operator": "OR",
            "conditions": [
                {
                    "field": "stage",
                    "operator": "IN",
                    "value": ["birth", "miscarriage", "stillbirth"],
                    "source_text": "출산/유산/사산",
                },
                {"field": "stage", "operator": "IN", "value": ["pregnant"]},
            ],
        },
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert _collect_leaf_fields(condition_json["condition_tree"]) == ["stage"]
    stage_leaf = _leaf_by_field(condition_json["condition_tree"], "stage")
    assert stage_leaf is not None and stage_leaf["value"] == "pregnant"
    birth = [
        i
        for i in condition_json["unsupported_conditions"]
        if i.get("reason") == REASON_BIRTH_EVENT_NOT_STAGE
    ]
    assert birth and birth[0]["field"] == "birth_event"
    assert QUALITY_VALIDATION_ERROR in condition_json["quality_flags"]
    assert saved["review_required"] is True


def test_validation_305_stage_exists_none_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """305. stage EXISTS None은 hard 조건이 될 수 없어 제거되고 빈 tree로 review."""
    target = _make_target(policy_id=305)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {"field": "stage", "operator": "EXISTS", "value": None}
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert condition_json["condition_tree"] == {}
    assert QUALITY_EMPTY_CONDITION_TREE in condition_json["quality_flags"]
    assert saved["review_required"] is True


def test_validation_253_child_age_none_list_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """253. child_age [None]은 제거한다."""
    target = _make_target(policy_id=253)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {"field": "child_age", "operator": "EQ", "value": [None]}
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert "child_age" not in _collect_leaf_fields(condition_json["condition_tree"])
    assert QUALITY_VALIDATION_ERROR in condition_json["quality_flags"]


def test_validation_332_child_age_strings_normalized_to_numbers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """332. "3세","4세","5세" 문자열을 숫자 배열로 정규화."""
    target = _make_target(policy_id=332)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {"field": "child_age", "operator": "IN", "value": ["3세", "4세", "5세"]}
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    leaf = _leaf_by_field(condition_json["condition_tree"], "child_age")
    assert leaf is not None and leaf["value"] == [3, 4, 5]
    assert QUALITY_VALIDATION_ADJUSTED in condition_json["quality_flags"]


def test_validation_279_birth_weight_not_child_age(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """279. 출생체중/재태기간은 child_age가 아니라 unsupported로 이동."""
    target = _make_target(policy_id=279)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {
                "field": "child_age",
                "operator": "LTE",
                "value": 2500,
                "source_text": "출생체중 2500g 미만",
            }
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert "child_age" not in _collect_leaf_fields(condition_json["condition_tree"])
    assert "clinical_measure" in _unsupported_fields(condition_json)


def test_validation_308_income_percent_dict_moved_to_median(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """308. income_status에 들어간 중위소득 percent dict를 median_income_percent로 교정."""
    target = _make_target(policy_id=308)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {
                "field": "income_status",
                "operator": "LTE",
                "value": {"percent": 250},
                "source_text": "가구 기준중위소득 250% 이하",
            }
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert "income_status" not in _collect_leaf_fields(condition_json["condition_tree"])
    leaf = _leaf_by_field(condition_json["condition_tree"], "median_income_percent")
    assert leaf is not None
    assert leaf["operator"] == "LTE"
    assert leaf["value"] == {"percent": 250}
    assert leaf["matching_strength"] == STRENGTH_HARD


def test_validation_269_asset_criterion_not_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """269. region에 저장된 자산 기준 금액을 unsupported(asset)로 이동."""
    target = _make_target(policy_id=269)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {
                "field": "region",
                "operator": "LTE",
                "value": {"amount": 242000000},
                "source_text": "대도시 거주자 재산 2억4200만원 이하",
            }
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert "region" not in _collect_leaf_fields(condition_json["condition_tree"])
    asset = [
        i
        for i in condition_json["unsupported_conditions"]
        if i.get("reason") == REASON_ASSET_NOT_REGION
    ]
    assert asset and asset[0]["field"] == "asset"


def test_validation_238_farmer_mapped_to_multichild_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """238. 농업인을 multichild로 억지 매핑한 special_condition을 hard에서 분리."""
    target = _make_target(policy_id=238)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {
                "field": "special_condition",
                "operator": "EQ",
                "value": "multichild",
                "source_text": "농업인",
            }
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    assert "special_condition" not in _collect_leaf_fields(
        condition_json["condition_tree"]
    )
    assert "special_condition" in _unsupported_fields(condition_json)
    assert REASON_FIELD_NOT_SUPPORTED in {
        i.get("reason") for i in condition_json["unsupported_conditions"]
    }


def test_validation_legit_special_condition_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정상적인 다자녀 special_condition은 보정 없이 그대로 hard로 유지."""
    target = _make_target(policy_id=999)
    extraction = _PolicyConditionExtractionModel(
        condition_tree=_single_leaf_tree(
            {
                "field": "special_condition",
                "operator": "EQ",
                "value": "multichild",
                "source_text": "다자녀 가구",
            }
        ),
    )

    saved = _run_ingest(monkeypatch, target=target, extraction=extraction)
    condition_json = saved["condition_json"]

    leaf = _leaf_by_field(condition_json["condition_tree"], "special_condition")
    assert leaf is not None and leaf["value"] == "multichild"
    assert QUALITY_VALIDATION_ERROR not in condition_json["quality_flags"]
    assert condition_json["unsupported_conditions"] == []


def test_validation_prompt_states_field_value_contracts() -> None:
    prompt = PolicyConditionProfileService()._system_prompt()

    assert "stage 허용값은 정확히 다음 5개" in prompt
    assert "출생체중" in prompt
    assert "child_age는 숫자 또는 숫자 배열만" in prompt
    assert 'median_income_percent + LTE {"percent": 250}' in prompt
    assert "자산·재산 기준 금액" in prompt

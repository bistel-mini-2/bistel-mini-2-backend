from app.services.policy_rule_filter_service import PolicyRuleFilterService


def test_policy_rule_filter_matches_income_limit() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"income": "mid1"},
        policy_rules=[
            {
                "field_name": "income",
                "operator": "LTE",
                "value_json": {"value": 100},
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "중위소득 100% 이하",
            }
        ],
    )

    assert result.matched_conditions == ["중위소득 100% 이하 충족"]
    assert result.rule_failures == []


def test_policy_rule_filter_excludes_hard_rule_mismatch() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"income": "high"},
        policy_rules=[
            {
                "field_name": "income",
                "operator": "LTE",
                "value_json": {"value": 100},
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "중위소득 100% 이하",
            }
        ],
    )

    assert result.matched_conditions == []
    assert result.rule_failures == ["중위소득 100% 이하 미충족"]


def test_policy_rule_filter_maps_income_to_median_income_percent() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"income": "mid1"},
        policy_rules=[
            {
                "field_name": "median_income_percent",
                "operator": "LTE",
                "value_json": {"value": 250},
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "기준중위소득 250% 이하",
            }
        ],
    )

    assert result.matched_conditions == ["기준중위소득 250% 이하 충족"]
    assert result.missing_conditions == []


def test_policy_rule_filter_maps_child_age_to_age_rule() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"childAge": "6-12"},
        policy_rules=[
            {
                "field_name": "age",
                "operator": "LTE",
                "value_json": {"value": 12},
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "12세 이하",
            }
        ],
    )

    assert result.matched_conditions == ["12세 이하 충족"]
    assert result.missing_conditions == []


def test_policy_rule_filter_marks_missing_hard_rule_field() -> None:
    result = PolicyRuleFilterService().filter(
        condition={},
        policy_rules=[
            {
                "field_name": "special",
                "operator": "IN",
                "value_json": ["many"],
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "다자녀 가구 대상",
            }
        ],
    )

    assert result.missing_conditions == ["다자녀 가구 대상 확인 필요"]


def test_policy_rule_filter_preserves_manual_check_rule() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"income": "mid1"},
        policy_rules=[
            {
                "field_name": "income",
                "operator": "EXISTS",
                "value_json": {"keyword": "저소득/수급 자격"},
                "is_hard_filter": False,
                "manual_check_required": True,
                "manual_check_reason": "정확한 소득 기준 확인 필요",
                "note": "저소득 조건",
            }
        ],
    )

    assert result.manual_check_points == ["정확한 소득 기준 확인 필요"]


def test_policy_rule_filter_merges_alternative_in_rules() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"stage": "newborn"},
        policy_rules=[
            {
                "field_name": "stage",
                "operator": "IN",
                "value_json": ["pregnant"],
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "lifeArray: 임신 · 출산",
            },
            {
                "field_name": "stage",
                "operator": "IN",
                "value_json": ["newborn", "infant"],
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "lifeArray: 영유아",
            },
        ],
    )

    assert result.rule_failures == []
    assert result.matched_conditions == [
        "lifeArray: 임신 · 출산, lifeArray: 영유아 충족"
    ]


def test_policy_rule_filter_reports_missing_alternative_rule_once() -> None:
    result = PolicyRuleFilterService().filter(
        condition={},
        policy_rules=[
            {
                "field_name": "special",
                "operator": "IN",
                "value_json": ["single"],
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "한부모",
            },
            {
                "field_name": "special",
                "operator": "IN",
                "value_json": ["many"],
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "다자녀",
            },
        ],
    )

    assert result.missing_conditions == ["한부모, 다자녀 확인 필요"]

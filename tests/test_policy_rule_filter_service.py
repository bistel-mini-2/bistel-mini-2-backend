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


def test_policy_rule_filter_does_not_truncate_manual_check_source_text() -> None:
    source_text = (
        "감당할 수 없는 빚으로 개인회생, 개인파산 및 면책 제도 이용을 원하는 사람"
    )
    result = PolicyRuleFilterService().filter(
        condition={},
        policy_rules=[
            {
                "field_name": "debt_status",
                "operator": "IN",
                "value_json": ["personal_bankruptcy_need"],
                "is_hard_filter": True,
                "manual_check_required": True,
                "manual_check_reason": None,
                "source_text": source_text,
            }
        ],
    )

    assert result.manual_check_points == [f"{source_text} 확인 필요"]


def test_policy_rule_filter_prefers_manual_check_source_text() -> None:
    source_text = "감당할 수 없는 빚으로 개인회생, 개인파산 및 면책 제도 이용을 원하는 사람"
    result = PolicyRuleFilterService().filter(
        condition={},
        policy_rules=[
            {
                "field_name": "debt_status",
                "operator": "IN",
                "value_json": ["personal_bankruptcy_need"],
                "is_hard_filter": True,
                "manual_check_required": True,
                "manual_check_reason": "사용자 입력에 대응 field가 없어 자동 매칭 불가",
                "source_text": source_text,
            }
        ],
    )

    assert result.manual_check_points == [f"{source_text} 확인 필요"]


def test_policy_rule_filter_excludes_review_required_manual_rule() -> None:
    result = PolicyRuleFilterService().filter(
        condition={},
        policy_rules=[
            {
                "field_name": "target",
                "operator": "IN",
                "value_json": ["unknown"],
                "is_hard_filter": True,
                "manual_check_required": True,
                "manual_check_reason": "수동 확인 필요",
                "source_text": "원문 기반 추가 확인 문구",
                "review_required": True,
            }
        ],
    )

    assert result.manual_check_points == []
    assert result.missing_conditions == []
    assert result.rule_failures == []


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


def test_policy_rule_filter_does_not_ask_manual_or_rule_when_alternative_matches() -> None:
    result = PolicyRuleFilterService().filter(
        condition={"region": "seoul"},
        policy_rules=[
            {
                "field_name": "region",
                "operator": "EQ",
                "value_json": "seoul",
                "is_hard_filter": True,
                "manual_check_required": False,
                "note": "서울 거주",
                "rule_group": "target_any",
                "group_operator": "OR",
                "source_text": "서울 거주",
            },
            {
                "field_name": "special",
                "operator": "IN",
                "value_json": ["multi"],
                "is_hard_filter": True,
                "manual_check_required": True,
                "manual_check_reason": "다자녀 여부 확인 필요",
                "source_text": "다자녀 가구",
                "rule_group": "target_any",
                "group_operator": "OR",
            },
        ],
    )

    assert result.matched_conditions == ["서울 거주 충족"]
    assert result.manual_check_points == []

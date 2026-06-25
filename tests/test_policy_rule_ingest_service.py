from app.services.policy_rule_ingest_service import PolicyRuleIngestService


def _rule_by(rules, field_name):
    return next(r for r in rules if r["field_name"] == field_name)


def _build(condition_json, *, raw="", confidence=0.9, review=False):
    return PolicyRuleIngestService().build_rules(
        condition_json=condition_json,
        profile_source_text=raw,
        profile_confidence=confidence,
        profile_review_required=review,
    )


# 236번 복합조건: 생계급여 수급가구 AND (임산부 OR 34세 이하)
_COMPOSITE = {
    "condition_tree": {
        "operator": "AND",
        "conditions": [
            {
                "operator": "AND",
                "group_key": "income",
                "conditions": [
                    {
                        "type": "income",
                        "field": "income_status",
                        "value": "basic_livelihood_recipient",
                        "operator": "EQ",
                        "source_text": "생계급여 수급가구",
                        "matching_strength": "hard",
                    }
                ],
            },
            {
                "operator": "OR",
                "group_key": "target",
                "conditions": [
                    {
                        "type": "stage",
                        "field": "stage",
                        "value": "pregnant",
                        "operator": "EQ",
                        "source_text": "임산부가 포함된 가구",
                        "matching_strength": "hard",
                    },
                    {
                        "type": "household_member_age",
                        "field": "household_member_age",
                        "value": 34,
                        "operator": "LTE",
                        "source_text": "34세 이하인 자",
                        "matching_strength": "hard",
                    },
                ],
            },
        ],
    }
}
_COMPOSITE_RAW = "생계급여 수급가구 임산부가 포함된 가구 또는 34세 이하인 자"


def test_composite_and_or_preserved_in_rule_groups() -> None:
    rules = _build(_COMPOSITE, raw=_COMPOSITE_RAW)

    assert len(rules) == 3

    income = _rule_by(rules, "income_status")
    assert income["rule_group"] == "income"
    assert income["group_operator"] == "AND"
    assert income["is_hard_filter"] is True
    assert income["is_exclusion"] is False
    assert income["operator"] == "EQ"

    stage = _rule_by(rules, "stage")
    age = _rule_by(rules, "household_member_age")
    # 같은 OR 그룹(target)에 묶여 group_operator=OR로 보존된다.
    assert stage["rule_group"] == "target"
    assert stage["group_operator"] == "OR"
    assert age["rule_group"] == "target"
    assert age["group_operator"] == "OR"
    assert age["operator"] == "LTE"
    assert age["value_json"] == 34


def test_root_level_leaf_uses_all_group() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "child_age",
                        "value": 2,
                        "operator": "LT",
                        "source_text": "2세 미만",
                        "matching_strength": "hard",
                    }
                ],
            }
        },
        raw="2세 미만 아동",
    )
    rule = _rule_by(rules, "child_age")
    assert rule["rule_group"] == "ALL"
    assert rule["group_operator"] == "AND"


def test_follow_up_strength_marks_manual_check() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "legal_consultation_need",
                        "value": "child_support_enforcement_need",
                        "operator": "EQ",
                        "source_text": "법률 상담이 필요한 국민",
                        "matching_strength": "follow_up",
                    }
                ],
            }
        },
        raw="법률 상담이 필요한 국민",
    )
    rule = _rule_by(rules, "legal_consultation_need")
    assert rule["is_hard_filter"] is False
    assert rule["manual_check_required"] is True
    assert rule["review_required"] is True


def test_vocabulary_normalized_to_enum() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "OR",
                "conditions": [
                    {
                        "field": "stage",
                        "value": "youth",
                        "operator": "EQ",
                        "source_text": "청소년",
                        "matching_strength": "soft",
                    },
                    {
                        "field": "special_condition",
                        "value": "disabled_household",
                        "operator": "EQ",
                        "source_text": "장애인",
                        "matching_strength": "hard",
                    },
                ],
            }
        },
        raw="청소년 장애인",
    )
    assert _rule_by(rules, "stage")["value_json"] == "teen"
    assert _rule_by(rules, "special_condition")["value_json"] == "disabled"


def test_special_condition_list_value_normalized() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "special_condition",
                        "value": ["single_parent_or_grandparent", "multichild"],
                        "operator": "IN",
                        "source_text": "한부모 다자녀",
                        "matching_strength": "hard",
                    }
                ],
            }
        },
        raw="한부모 다자녀",
    )
    assert _rule_by(rules, "special_condition")["value_json"] == ["single", "many"]


def test_exclusion_stored_as_is_exclusion() -> None:
    rules = _build(
        {
            "condition_tree": {},
            "exclusions": [
                {
                    "type": "income",
                    "field": "income_status",
                    "value": "medical_benefit_recipient",
                    "operator": "EQ",
                    "source_text": "의료급여 수급자는 제외",
                    "reason": "EXPLICIT_EXCLUSION",
                }
            ],
        },
        raw="의료급여 수급자는 제외",
    )
    assert len(rules) == 1
    exclusion = rules[0]
    assert exclusion["is_exclusion"] is True
    assert exclusion["manual_check_required"] is True
    assert exclusion["is_hard_filter"] is False
    assert exclusion["rule_group"] == "EXCLUSION"


def test_source_text_not_in_raw_marks_manual() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "median_income_percent",
                        "value": {"percent": 75},
                        "operator": "LTE",
                        "source_text": "기준 중위소득 999% 이하",  # 원문에 없음
                        "matching_strength": "hard",
                    }
                ],
            }
        },
        raw="기준 중위소득 75% 이하",
    )
    rule = _rule_by(rules, "median_income_percent")
    assert rule["is_hard_filter"] is False
    assert rule["manual_check_required"] is True
    assert rule["review_required"] is True
    assert "환각" in (rule["manual_check_reason"] or "")


def test_unknowns_and_unsupported_preserved_as_manual() -> None:
    rules = _build(
        {
            "condition_tree": {},
            "unknowns": [
                {"field": "debt_status", "value": None, "reason": "FIELD_NOT_SUPPORTED"}
            ],
            "unsupported_conditions": [
                {
                    "field": "nationality_status",
                    "value": ["registered_foreigner"],
                    "reason": "SERVICE_FIELD_NOT_SUPPORTED",
                }
            ],
        }
    )
    assert len(rules) == 2
    assert all(r["manual_check_required"] for r in rules)
    assert all(r["review_required"] for r in rules)
    assert {r["rule_group"] for r in rules} == {"UNKNOWN", "UNSUPPORTED"}


def test_low_confidence_demoted_to_manual() -> None:
    rules = _build(
        {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "stage",
                        "value": "child",
                        "operator": "EQ",
                        "source_text": "아동",
                        "matching_strength": "hard",
                        "confidence": 0.3,
                    }
                ],
            }
        },
        raw="아동",
    )
    rule = _rule_by(rules, "stage")
    assert rule["is_hard_filter"] is False
    assert rule["manual_check_required"] is True

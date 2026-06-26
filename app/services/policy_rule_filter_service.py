from dataclasses import dataclass, field
from typing import Any

from app.services.policy_rule_grouping import (
    INCOME_LEVEL_TO_PERCENT,
    OUTCOME_FAIL,
    OUTCOME_MANUAL,
    OUTCOME_MATCH,
    VERDICT_MATCH,
    evaluate_or_group,
    group_note,
    partition_or_groups,
    rule_matches,
    rule_values,
    to_number,
)

# 하위 호환: 기존에 이 모듈에서 INCOME_LEVEL_TO_PERCENT를 import하던 코드 보존.
__all__ = ["PolicyRuleFilterService", "PolicyRuleFilterResult", "INCOME_LEVEL_TO_PERCENT"]


@dataclass
class PolicyRuleFilterResult:
    matched_conditions: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    rule_failures: list[str] = field(default_factory=list)
    manual_check_points: list[str] = field(default_factory=list)


class PolicyRuleFilterService:
    def filter(
        self,
        condition: dict[str, Any],
        policy_rules: list[dict[str, Any]],
    ) -> PolicyRuleFilterResult:
        result = PolicyRuleFilterResult()
        # 서로 다른 field의 OR(대안) 그룹은 별도로 묶어 평가한다(같은 field IN 병합만으로는
        # "특수상황=multi OR 생애주기=teen"을 풀 수 없음).
        flat_rules, or_groups = partition_or_groups(policy_rules)

        for rule in self._merge_alternative_rules(flat_rules):
            field_name = str(rule.get("field_name") or "").strip()
            if not field_name:
                continue

            condition_value = self._condition_value(condition, field_name)
            rule_value = rule.get("value_json")
            operator = str(rule.get("operator") or "").upper()
            note = str(rule.get("note") or field_name)

            if rule.get("manual_check_required") is True:
                result.manual_check_points.append(
                    str(rule.get("manual_check_reason") or note)
                )
                continue

            if condition_value in (None, "", []):
                if rule.get("is_hard_filter") is True:
                    result.missing_conditions.append(field_name)
                continue

            match_result = self._rule_matches(operator, condition_value, rule_value)
            if match_result is True:
                result.matched_conditions.append(note)
            elif match_result is None:
                result.manual_check_points.append(note)
            elif rule.get("is_hard_filter") is True:
                result.rule_failures.append(note)

        for group_key, group_rules in or_groups:
            self._apply_or_group(condition, group_key, group_rules, result)
        return result

    def _apply_or_group(
        self,
        condition: dict[str, Any],
        group_key: str,
        group_rules: list[dict[str, Any]],
        result: PolicyRuleFilterResult,
    ) -> None:
        outcome, verdicts = evaluate_or_group(
            group_rules,
            condition,
            self._condition_value,
            self._rule_matches,
        )
        if outcome == OUTCOME_MATCH:
            # 충족된 대안만 matched로 보고한다(나머지 대안의 missing은 그룹이 통과했으므로 무시).
            for rule, verdict, _, _ in verdicts:
                if verdict == VERDICT_MATCH:
                    result.matched_conditions.append(
                        str(rule.get("note") or rule.get("field_name"))
                    )
        elif outcome == OUTCOME_MANUAL:
            result.manual_check_points.append(group_note(group_rules, group_key))
        elif outcome == OUTCOME_FAIL:
            result.rule_failures.append(group_note(group_rules, group_key))

    def _merge_alternative_rules(
        self,
        policy_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: dict[tuple[str, str, bool, bool], dict[str, Any]] = {}
        passthrough: list[dict[str, Any]] = []
        for rule in policy_rules:
            operator = str(rule.get("operator") or "").upper()
            if operator != "IN" or rule.get("manual_check_required") is True:
                passthrough.append(rule)
                continue

            field_name = str(rule.get("field_name") or "")
            key = (
                field_name,
                operator,
                bool(rule.get("is_hard_filter")),
                bool(rule.get("manual_check_required")),
            )
            if key not in merged:
                merged[key] = {
                    **rule,
                    "value_json": [],
                    "note": [],
                }
            merged[key]["value_json"].extend(self._rule_values(rule.get("value_json")))
            if rule.get("note"):
                merged[key]["note"].append(str(rule["note"]))

        normalized = []
        for rule in merged.values():
            rule = {**rule}
            rule["value_json"] = list(dict.fromkeys(rule["value_json"]))
            rule["note"] = ", ".join(dict.fromkeys(rule["note"])) or rule.get("field_name")
            normalized.append(rule)
        return passthrough + normalized

    def _condition_value(self, condition: dict[str, Any], field_name: str) -> Any:
        stage_keys = ("stage", "target_stage", "life_stage")
        child_age_keys = ("childAge", "child_age", "child_age_range")
        income_keys = ("income", "income_level", "income_bracket")
        region_keys = ("region", "region_code")
        special_keys = ("special", "special_conditions", "special_flags", "special_condition")
        # RecommendationCandidateService._condition_value와 동일한 alias 키 집합을 유지한다.
        key_groups = {
            "stage": stage_keys,
            "life_stage": stage_keys,
            "target_stage": stage_keys,
            "childAge": child_age_keys,
            "child_age": child_age_keys,
            "child_age_range": child_age_keys,
            "income": income_keys,
            "income_level": income_keys,
            "income_status": ("income_status", "benefit_status"),
            "region": region_keys,
            "region_code": region_keys,
            "special": special_keys,
            "special_flags": special_keys,
            "special_condition": special_keys,
            "age": ("age", "user_age"),
            "household_member_age": (
                "household_member_age",
                "household_member_ages",
                "household_ages",
            ),
        }
        for key in key_groups.get(field_name, (field_name,)):
            value = condition.get(key)
            if value not in (None, "", []):
                return value
        return None

    # 매처는 RecommendationCandidateService와 동일 동작을 보장하기 위해 공유 모듈에 위임한다.
    def _rule_matches(
        self,
        operator: str,
        condition_value: Any,
        rule_value: Any,
    ) -> bool | None:
        return rule_matches(operator, condition_value, rule_value)

    def _rule_values(self, rule_value: Any) -> list[Any]:
        return rule_values(rule_value)

    def _to_number(self, value: Any) -> float | None:
        return to_number(value)

from dataclasses import dataclass, field
from typing import Any


INCOME_LEVEL_TO_PERCENT = {
    "low": 50.0,
    "mid1": 100.0,
    "mid2": 150.0,
    "high": 200.0,
}


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
        for rule in self._merge_alternative_rules(policy_rules):
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
        return result

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
        key_groups = {
            "stage": ("stage", "target_stage", "life_stage"),
            "childAge": ("childAge", "child_age", "child_age_range"),
            "child_age": ("childAge", "child_age", "child_age_range"),
            "income": ("income", "income_level", "income_bracket"),
            "income_level": ("income", "income_level", "income_bracket"),
            "region": ("region", "region_code"),
            "special": ("special", "special_conditions"),
        }
        for key in key_groups.get(field_name, (field_name,)):
            value = condition.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _rule_matches(
        self,
        operator: str,
        condition_value: Any,
        rule_value: Any,
    ) -> bool | None:
        values = self._rule_values(rule_value)
        if operator == "EQ":
            return str(condition_value) == str(values[0]) if values else None
        if operator == "IN":
            condition_values = (
                {str(item) for item in condition_value}
                if isinstance(condition_value, list)
                else {str(condition_value)}
            )
            return bool(condition_values & {str(value) for value in values})
        if operator in {"GTE", "LTE"}:
            condition_number = self._to_number(condition_value)
            rule_number = self._to_number(values[0] if values else None)
            if condition_number is None or rule_number is None:
                return None
            if operator == "GTE":
                return condition_number >= rule_number
            return condition_number <= rule_number
        if operator == "EXISTS":
            return condition_value not in (None, "", [])
        return None

    def _rule_values(self, rule_value: Any) -> list[Any]:
        if isinstance(rule_value, list):
            return rule_value
        if isinstance(rule_value, dict):
            for key in ("value", "values", "allowed", "in"):
                value = rule_value.get(key)
                if isinstance(value, list):
                    return value
                if value is not None:
                    return [value]
            return list(rule_value.values())
        return [rule_value]

    def _to_number(self, value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        mapped = INCOME_LEVEL_TO_PERCENT.get(str(value).strip())
        if mapped is not None:
            return mapped
        try:
            return float(str(value).strip().replace("%", ""))
        except ValueError:
            return None

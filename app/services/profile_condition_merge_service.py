from typing import Any

from app.common.policy_types import INCOME_LEVEL_TO_DB
from app.schemas.ai_contract import ProfileConflict


INCOME_DB_TO_LEVEL = {
    value: key for key, value in INCOME_LEVEL_TO_DB.items() if value is not None
}
CORE_RULE_FIELDS = {"stage", "childAge", "income", "region", "special"}


class ProfileConditionMergeService:
    @staticmethod
    def merge(
        parsed_condition: dict[str, Any],
        profile_snapshot: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], list[ProfileConflict]]:
        profile_condition = ProfileConditionMergeService._normalize_snapshot(
            profile_snapshot or {}
        )
        current_condition = ProfileConditionMergeService._normalize_snapshot(
            parsed_condition
        )

        merged: dict[str, Any] = {}
        conflicts: list[ProfileConflict] = []
        for field_name in ["stage", "childAge", "income", "region", "special"]:
            saved_value = profile_condition.get(field_name)
            current_value = current_condition.get(field_name)
            selected_value = (
                current_value if current_value not in (None, "", []) else saved_value
            )

            if selected_value not in (None, "", []):
                merged[field_name] = selected_value

            if (
                saved_value not in (None, "", [])
                and current_value not in (None, "", [])
                and saved_value != current_value
            ):
                conflicts.append(
                    ProfileConflict(
                        field_name=field_name,
                        saved_value=saved_value,
                        current_value=current_value,
                        selected_value=current_value,
                        changes_rule_filter_result=field_name in CORE_RULE_FIELDS,
                    )
                )

        for field_name in [
            "needs",
            "household_type",
            "employment_status",
            "pregnancy_status",
        ]:
            saved_value = profile_condition.get(field_name)
            current_value = current_condition.get(field_name)
            selected_value = (
                current_value if current_value not in (None, "", []) else saved_value
            )
            if selected_value not in (None, "", []):
                merged[field_name] = selected_value

        return ProfileConditionMergeService.expand_condition_aliases(merged), conflicts

    @staticmethod
    def expand_condition_aliases(condition: dict[str, Any]) -> dict[str, Any]:
        special = list(dict.fromkeys(condition.get("special") or []))
        expanded = {
            **condition,
            "life_stage": condition.get("stage"),
            "target_stage": condition.get("stage"),
            "child_age": condition.get("childAge"),
            "child_age_range": condition.get("childAge"),
            "income_level": condition.get("income"),
            "income_bracket": INCOME_LEVEL_TO_DB.get(str(condition.get("income"))),
            "region_code": condition.get("region"),
            "special": special,
            "special_flags": special,
        }
        if "dual" in special:
            expanded["employment_status"] = "dual_income"
        if condition.get("stage") == "pregnant" or condition.get("childAge") == "preborn":
            expanded["pregnancy_status"] = True
        return {
            key: value for key, value in expanded.items() if value not in (None, "", [])
        }

    @staticmethod
    def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        aliases = {
            "life_stage": "stage",
            "target_stage": "stage",
            "child_age": "childAge",
            "child_age_range": "childAge",
            "income_level": "income",
            "income_bracket": "income",
            "region_code": "region",
            "special_conditions": "special",
            "special_flags": "special",
        }
        for key, value in snapshot.items():
            normalized_key = aliases.get(key, key)
            if normalized_key == "income" and key == "income_bracket":
                value = INCOME_DB_TO_LEVEL.get(str(value), value)
            if normalized_key == "special" and value is None:
                value = []
            normalized[normalized_key] = value

        special = normalized.get("special")
        if isinstance(special, list):
            normalized["special"] = list(dict.fromkeys(special))
        return normalized

from typing import Any

from app.common.policy_types import INCOME_LEVEL_TO_DB
from app.schemas.ai_contract import ProfileConflict


INCOME_DB_TO_LEVEL = {
    value: key for key, value in INCOME_LEVEL_TO_DB.items() if value is not None
}
CORE_RULE_FIELDS = {"stage", "childAge", "income", "region", "special"}
# policy_rule 판정 결과를 바꾸는 추가 입력. 저장값과 현재값이 다르면 conflict로 표시한다.
RULE_AFFECTING_EXTRA_FIELDS = ["income_status", "age", "household_member_age"]

# 세부 급여 수급자격은 "기초생활수급자(basic)"의 하위 범주다. 정책 룰은 일부는
# basic_livelihood_recipient(우산), 일부는 medical/housing 같은 세부값을 직접 요구하므로,
# 세부값을 basic으로 '치환'하면 세부값을 요구하는 정책(예: 의료급여 임신출산)이 깨진다.
# 따라서 세부값은 유지하면서 basic을 함께 '확장'해 둘 다 매칭되게 한다.
_INCOME_STATUS_BROADENS_TO_BASIC = {
    "medical_benefit_recipient",
    "housing_benefit_recipient",
    "education_benefit_recipient",
    "livelihood_benefit_recipient",
}
_INCOME_STATUS_BASIC = "basic_livelihood_recipient"


def _expand_income_status(value: Any) -> Any:
    """세부 급여 수급자격에 우산값(basic_livelihood_recipient)을 함께 부여한다."""
    items = value if isinstance(value, list) else [value]
    expanded: list[str] = []
    for item in items:
        token = str(item).strip()
        if not token:
            continue
        expanded.append(token)
        if token in _INCOME_STATUS_BROADENS_TO_BASIC:
            expanded.append(_INCOME_STATUS_BASIC)
    deduped = list(dict.fromkeys(expanded))
    if not deduped:
        return value
    # 단일 값이고 확장 안 된 경우 원형(스칼라) 유지, 그 외 리스트.
    return deduped[0] if len(deduped) == 1 else deduped


def _values_equal(left: Any, right: Any) -> bool:
    """conflict 판정용 동등 비교. 리스트는 순서 무관(집합 의미)으로 본다."""
    if isinstance(left, list) and isinstance(right, list):
        return sorted(map(str, left)) == sorted(map(str, right))
    return left == right


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
                and not _values_equal(saved_value, current_value)
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

        # policy_rule 판정에 영향을 주는 신규 입력: 통과 + conflict 추적(영향 있음 표시).
        for field_name in RULE_AFFECTING_EXTRA_FIELDS:
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
                and not _values_equal(saved_value, current_value)
            ):
                conflicts.append(
                    ProfileConflict(
                        field_name=field_name,
                        saved_value=saved_value,
                        current_value=current_value,
                        selected_value=current_value,
                        changes_rule_filter_result=True,
                    )
                )

        # 판정에 직접 영향 없는 보조 입력(있을 때만 통과, conflict 미추적).
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
            "special_condition": "special",
            # policy_rule 자동 판정용 신규 입력의 다양한 표기를 표준 키로 통일.
            "benefit_status": "income_status",
            "user_age": "age",
            "household_member_ages": "household_member_age",
            "household_ages": "household_member_age",
        }
        for key, value in snapshot.items():
            normalized_key = aliases.get(key, key)
            if normalized_key == "income" and key == "income_bracket":
                value = INCOME_DB_TO_LEVEL.get(str(value), value)
            if normalized_key == "special":
                # special_condition 등 단일 문자열로 들어오면 글자 단위로 쪼개지지
                # 않도록 리스트로 감싼다(이후 dict.fromkeys/expand에서 안전).
                if value is None:
                    value = []
                elif isinstance(value, str):
                    value = [value]
            if normalized_key == "income_status" and value not in (None, "", []):
                value = _expand_income_status(value)
            normalized[normalized_key] = value

        special = normalized.get("special")
        if isinstance(special, list):
            normalized["special"] = list(dict.fromkeys(special))
        return normalized

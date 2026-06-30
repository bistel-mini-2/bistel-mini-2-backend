from typing import Any

from app.services.policy_rule_grouping import INCOME_LEVEL_TO_PERCENT, to_number


# 입력 조건(condition)에서 policy_rule field를 읽을 때, 추천 후보 필터
# (RecommendationCandidateService)와 지원가능성 룰 필터(PolicyRuleFilterService)가
# 동일하게 해석하도록 alias/정규화를 한 곳에서 관리한다.
# 여기서 alias를 한 번만 바꾸면 두 서비스가 자동으로 같이 맞춰진다.
_STAGE = ("stage", "life_stage", "target_stage")
_CHILD_AGE = ("childAge", "child_age", "child_age_range")
_INCOME = ("income", "income_level", "income_bracket")
_REGION = ("region", "region_code")
_SPECIAL = ("special", "special_flags", "special_conditions", "special_condition")
_INCOME_STATUS = ("income_status", "benefit_status")
_INCOME_FIELDS = frozenset(
    {"income", "income_level", "income_bracket", "median_income_percent"}
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "stage": _STAGE,
    "life_stage": _STAGE,
    "target_stage": _STAGE,
    "childAge": _CHILD_AGE,
    "child_age": _CHILD_AGE,
    "child_age_range": _CHILD_AGE,
    "income": _INCOME,
    "income_level": _INCOME,
    "income_bracket": _INCOME,
    # 직접 percent 값이 있으면 우선 사용하고, 없으면 소득 구간 코드로 대체한다.
    "median_income_percent": ("median_income_percent",) + _INCOME,
    "income_status": _INCOME_STATUS,
    "benefit_status": _INCOME_STATUS,
    "region": _REGION,
    "region_code": _REGION,
    "special": _SPECIAL,
    "special_flags": _SPECIAL,
    "special_conditions": _SPECIAL,
    "special_condition": _SPECIAL,
    # age는 신청자 본인 나이만 본다. childAge를 fallback으로 쓰면
    # 청소년산모처럼 신청자 연령을 따지는 정책이 자녀 나이로 잘못 매칭된다.
    "age": ("age", "user_age"),
    # 가구원 나이만(자녀 나이 fallback 없음).
    "household_member_age": (
        "household_member_age",
        "household_member_ages",
        "household_ages",
    ),
}


def condition_value(condition: dict[str, Any], field_name: str) -> Any:
    """condition에서 field 값을 alias 우선순위대로 찾는다(없으면 None)."""
    for key in _FIELD_ALIASES.get(field_name, (field_name,)):
        value = condition.get(key)
        if value not in (None, "", []):
            return value
    return None


# 추천 폼이 "빠짐없이" 제공하는 다중선택 필드. 사용자가 옵션을 모두 보고 아무것도
# 선택하지 않았으면 "해당 없음"을 명시한 것이다(미입력=알 수 없음과 구분).
_EXHAUSTIVE_FORM_FIELDS = {"special", "special_flags", "special_conditions", "special_condition"}


def is_exhaustive_empty(condition: dict[str, Any], field_name: str) -> bool:
    """폼이 빠짐없이 받는 필드(special)가 'condition에 키는 있으나 빈 값'인 경우.

    True면 "사용자가 그 특수상황에 해당 없음을 명시"한 것으로 보고, 미입력(불확정)이
    아니라 확정 미일치로 처리한다. 키 자체가 없으면(채팅 등 미제공) False → 기존대로 불확정.
    """
    if field_name not in _EXHAUSTIVE_FORM_FIELDS:
        return False
    for key in _FIELD_ALIASES.get(field_name, (field_name,)):
        if key in condition:
            return condition.get(key) in (None, "", [])
    return False


def normalize_condition_value(field_name: str, value: Any) -> Any:
    """소득 도메인 값은 구간 코드("mid1")를 중위소득 %로 변환해 비교 일관성을 맞춘다.

    예: income="mid1" → 100.0 (정책이 median_income_percent LTE 250이면 통과 가능).
    그 외 field/값은 그대로 둔다(매칭 시 policy_rule_grouping.to_number가 처리).
    """
    if value in (None, "", [], "unknown"):
        return None
    if field_name in _INCOME_FIELDS:
        normalized = str(value).strip()
        if normalized in INCOME_LEVEL_TO_PERCENT:
            return float(INCOME_LEVEL_TO_PERCENT[normalized])
        return to_number(normalized)
    return value

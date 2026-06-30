"""policy_rule의 rule_group / group_operator 기반 OR 그룹 평가 헬퍼.

#154에서 condition_tree의 AND/OR를 rule_group(group_key)·group_operator로 보존한다.
같은 field IN 병합만으로는 "특수상황=multi OR 생애주기=teen"처럼 서로 다른 field의 OR를
풀 수 없으므로, 같은 rule_group 안의 OR 멤버를 대안으로 평가한다.

matcher / condition_value 해석은 소비처(PolicyRuleFilterService,
RecommendationCandidateService)마다 약간 다르므로 콜러블로 주입받는다.
"""

from typing import Any, Callable

ConditionValueFn = Callable[[dict[str, Any], str], Any]
RuleMatchesFn = Callable[[str, Any, Any], "bool | None"]

# 사용자 income 구간 → 기준 중위소득 %. 수치 비교(LTE/GTE/LT/GT) 시 양 값을 %로 환산한다.
INCOME_LEVEL_TO_PERCENT: dict[str, float] = {
    "low": 50.0,
    "mid1": 100.0,
    "mid2": 150.0,
    "high": 200.0,
}

# income 구간이 실제로 나타내는 기준중위소득 % 범위 (하한 초과, 상한 이하).
# 사용자 입력이 4단계뿐이라 구간 내부를 구분할 수 없으므로, 정책 기준이 구간을 가로지르면
# 자동 탈락/통과가 아니라 None(=수동 확인)으로 처리해 저소득층이 통째로 빠지는 것을 막는다.
INCOME_LEVEL_RANGE: dict[str, tuple[float, float]] = {
    "low": (0.0, 50.0),
    "mid1": (50.0, 100.0),
    "mid2": (100.0, 150.0),
    "high": (150.0, float("inf")),
}

# income_status 계층: 세부 급여 수급자격(medical/housing/education/livelihood)은
# basic_livelihood_recipient(기초생활수급)의 하위 범주다. 세부값 보유자는 merge에서
# [세부값, basic]으로 확장되므로 세부/광의 정책 모두 match된다. 반대로 사용자가 광의(basic)
# 만 가진 경우 세부값을 요구하는 정책은 "확정 불가"이므로 False(탈락)가 아니라 None(수동 확인).
INCOME_STATUS_BASIC = "basic_livelihood_recipient"
INCOME_STATUS_SPECIFIC = {
    "medical_benefit_recipient",
    "housing_benefit_recipient",
    "education_benefit_recipient",
    "livelihood_benefit_recipient",
}


def _as_str_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {str(item) for item in value}
    return {str(value)}


def _income_bucket_match(
    operator: str,
    lower: float,
    upper: float,
    threshold: float,
) -> "bool | None":
    """income 버킷 범위 (lower 초과 ~ upper 이하)를 정책 기준값과 비교한다.

    - 구간 전체가 조건을 만족 → True
    - 구간 전체가 불만족 → False
    - 구간이 기준을 가로지름(일부만 만족) → None(수동 확인)
    """
    if operator == "LTE":
        if upper <= threshold:
            return True
        if lower >= threshold:
            return False
        return None
    if operator == "LT":
        if upper < threshold:
            return True
        if lower >= threshold:
            return False
        return None
    if operator == "GTE":
        if lower >= threshold:
            return True
        if upper < threshold:
            return False
        return None
    # GT
    if lower >= threshold:
        return True
    if upper <= threshold:
        return False
    return None


# 사용자 child_age 버킷(ChildAge enum) → 만 나이 [최소, 최대] 정수 범위(포함).
# 정책 룰의 숫자 연령 기준(child_age LT/LTE/GT/GTE n)과 보수적으로 비교한다.
# "preborn"은 임신 전 단계라 나이 비교 대상이 아니므로 제외 → to_number=None → manual.
CHILD_AGE_RANGE: dict[str, tuple[float, float]] = {
    "0": (0.0, 0.0),
    "1": (1.0, 1.0),
    "2-5": (2.0, 5.0),
    "6-12": (6.0, 12.0),
    "13+": (13.0, float("inf")),
}


def _child_age_bucket_match(
    operator: str,
    lower: float,
    upper: float,
    threshold: float,
) -> "bool | None":
    """child_age 버킷 [lower, upper](포함)를 정책 기준값과 비교한다.

    - 버킷 전체가 조건 만족 → True
    - 버킷 전체가 불만족 → False
    - 버킷이 기준을 가로지름 → None(수동 확인, 잘못 탈락 방지)
    """
    if operator == "LTE":
        if upper <= threshold:
            return True
        if lower > threshold:
            return False
        return None
    if operator == "LT":
        if upper < threshold:
            return True
        if lower >= threshold:
            return False
        return None
    if operator == "GTE":
        if lower >= threshold:
            return True
        if upper < threshold:
            return False
        return None
    # GT
    if lower > threshold:
        return True
    if upper <= threshold:
        return False
    return None


def _numeric_compare(operator: str, value: Any, threshold: float) -> "bool | None":
    """단일 사용자 값을 정책 수치 기준과 비교. income/child_age 버킷은 범위 비교."""
    key = str(value).strip()
    income_bucket = INCOME_LEVEL_RANGE.get(key)
    if income_bucket is not None:
        return _income_bucket_match(operator, income_bucket[0], income_bucket[1], threshold)
    child_bucket = CHILD_AGE_RANGE.get(key)
    if child_bucket is not None:
        return _child_age_bucket_match(operator, child_bucket[0], child_bucket[1], threshold)
    number = to_number(value)
    if number is None:
        return None
    if operator == "GTE":
        return number >= threshold
    if operator == "LTE":
        return number <= threshold
    if operator == "GT":
        return number > threshold
    return number < threshold


def _numeric_any_of(operator: str, values: list[Any], threshold: float) -> "bool | None":
    """가구원 연령 리스트처럼 여러 값 중 하나라도 만족하면 True(any-of).

    하나라도 만족 → True / 전부 확정 불만족 → False / 일부 평가 불가 → None.
    """
    results = [_numeric_compare(operator, item, threshold) for item in values]
    if any(result is True for result in results):
        return True
    if any(result is None for result in results):
        return None
    return False


def _child_age_in_set(
    lower: float,
    upper: float,
    allowed_numbers: list[float],
) -> "bool | None":
    """child_age 버킷 [lower, upper](포함)를 정책 IN 정수 집합과 비교.

    연속 범위가 아닌 비연속 집합(예: [2, 5])도 정확히 처리한다.
    - 버킷의 모든 나이가 집합에 포함 → True
    - 일부만 겹침 → None(수동 확인) / 겹침 없음 → False
    - "13+"처럼 상한이 무한이면 전체 포함은 불가 → 겹치면 None, 아니면 False
    """
    allowed = {int(number) for number in allowed_numbers}
    if not allowed:
        return None
    if upper == float("inf"):
        return None if any(number >= lower for number in allowed) else False
    bucket = set(range(int(lower), int(upper) + 1))
    if bucket <= allowed:
        return True
    if bucket & allowed:
        return None
    return False


def rule_values(rule_value: Any) -> list[Any]:
    """value_json을 비교용 list로 정규화한다(scalar/list/dict 모두 허용)."""
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


def to_number(value: Any) -> float | None:
    """수치 비교용으로 값을 float로 환산한다. income 구간명은 %로 매핑."""
    if value in (None, "", []):
        return None
    mapped = INCOME_LEVEL_TO_PERCENT.get(str(value).strip())
    if mapped is not None:
        return float(mapped)
    try:
        return float(str(value).strip().replace("%", ""))
    except (TypeError, ValueError):
        return None


def rule_matches(
    operator: str,
    condition_value: Any,
    rule_value: Any,
) -> "bool | None":
    """단일 rule 비교. 두 소비처가 동일하게 쓰는 canonical matcher.

    지원 operator: EQ, IN, GTE, LTE, GT, LT, EXISTS.
    값이 없거나 비교 불가하면 None(=수동 확인) 을 반환해 자동 탈락을 피한다.
    """
    operator = str(operator or "").upper()
    values = rule_values(rule_value)

    if operator == "EQ":
        if not values:
            return None
        # 사용자 값이 리스트(예: income_status 복수 수급자격)면 any-of로 비교.
        target = str(values[0])
        user_set = _as_str_set(condition_value)
        if target in user_set:
            return True
        # income_status 계층: 세부값 요구인데 사용자는 광의(basic)만 → 확정 불가(None).
        if target in INCOME_STATUS_SPECIFIC and INCOME_STATUS_BASIC in user_set:
            return None
        return False
    if operator == "IN":
        # 정책이 child_age IN [3,4,5]처럼 숫자 집합이고 사용자가 버킷("2-5")이면
        # 문자열 exact match가 아니라 정수 집합 포함으로 비교한다(잘못 탈락 방지).
        child_bucket = CHILD_AGE_RANGE.get(str(condition_value).strip())
        if child_bucket is not None:
            numbers = [to_number(value) for value in values]
            if numbers and all(number is not None for number in numbers):
                return _child_age_in_set(child_bucket[0], child_bucket[1], numbers)
        targets = {str(value) for value in values}
        user_set = _as_str_set(condition_value)
        if user_set & targets:
            return True
        # income_status 계층: 세부값을 요구하는데 사용자는 광의(basic)만 → None.
        if (targets & INCOME_STATUS_SPECIFIC) and INCOME_STATUS_BASIC in user_set:
            return None
        return False
    if operator in {"GTE", "LTE", "GT", "LT"}:
        rule_number = to_number(values[0] if values else None)
        if rule_number is None:
            return None
        # 가구원 연령 등 리스트 입력은 any-of, 단일 값은 income/child_age 버킷 범위 비교.
        # 버킷이 기준을 가로지르면 None(수동 확인) → 잘못 탈락 방지.
        if isinstance(condition_value, list):
            return _numeric_any_of(operator, condition_value, rule_number)
        return _numeric_compare(operator, condition_value, rule_number)
    if operator == "EXISTS":
        return condition_value not in (None, "", [])
    return None

# 멤버 평가 결과(verdict).
VERDICT_MATCH = "match"
VERDICT_FAIL = "fail"
VERDICT_MANUAL = "manual"
VERDICT_MISSING = "missing"  # hard 조건인데 사용자 입력이 없음
VERDICT_SKIP = "skip"  # soft 조건인데 입력이 있고 불일치(확정적 미일치)
# soft 조건인데 사용자 입력이 없어 평가 불가(불확정). soft 미일치(SKIP)와 구분해야
# "hard 실패 + soft 입력없음" OR 그룹을 fail이 아닌 manual로 구제할 수 있다.
VERDICT_INDETERMINATE = "indeterminate"

# OR 그룹 종합 결과(outcome).
OUTCOME_MATCH = "match"
OUTCOME_FAIL = "fail"
OUTCOME_MANUAL = "manual"
OUTCOME_SKIP = "skip"


def partition_or_groups(
    rules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, list[dict[str, Any]]]]]:
    """(평탄 처리 rule, OR 그룹 목록)으로 분리한다.

    분리 기준은 rule_group 문자열이 아니라 **각 rule의 group_operator**다.
    - group_operator=AND/공백 → 필수(AND) rule이므로, 같은 rule_group이라도 평탄 처리한다.
    - group_operator=OR → 같은 rule_group끼리 묶어 대안(OR) 집합으로 만든다.
    이렇게 하면 region=seoul AND (stage=teen OR special=multi)가 모두 rule_group=ALL이어도
    region(AND)은 필수로 남고 stage/special(OR)만 대안으로 평가된다.

    멤버가 1개뿐인 OR 집합은 대안이 아니라 단일 조건이므로 평탄 처리한다.
    """
    or_buckets: dict[str, list[dict[str, Any]]] = {}
    or_order: list[str] = []
    flat: list[dict[str, Any]] = []

    for rule in rules:
        if str(rule.get("group_operator") or "").upper() == "OR":
            key = str(rule.get("rule_group") or "ALL")
            if key not in or_buckets:
                or_buckets[key] = []
                or_order.append(key)
            or_buckets[key].append(rule)
        else:
            flat.append(rule)

    or_groups: list[tuple[str, list[dict[str, Any]]]] = []
    for key in or_order:
        bucket = or_buckets[key]
        if len(bucket) > 1:
            or_groups.append((key, bucket))
        else:
            flat.extend(bucket)
    return flat, or_groups


def is_income_status_denied(condition_value: Any) -> bool:
    """사용자가 수급 자격을 명시적으로 부정한 경우(income_status='none').

    주의: None(미입력)은 부정이 아니다. str(None)=='None'이라 소문자화하면 'none'과
    같아지므로, None은 명시적으로 먼저 걸러 미입력을 부정으로 오인하지 않게 한다.
    """
    if condition_value is None:
        return False
    if isinstance(condition_value, (list, tuple, set)):
        return any(
            v is not None and str(v).strip().lower() == "none" for v in condition_value
        )
    return str(condition_value).strip().lower() == "none"


def is_denied_income_mismatch(
    field_name: str, condition_value: Any, rule_value: Any
) -> bool:
    """수급 자격을 '없다'고 명시(income_status='none')했는데 정책이 특정 수급 자격을
    요구(rule_value에 none 미포함)하면, hard filter 여부와 무관하게 명확한 미충족이다.

    flat rule 경로(candidate/filter)와 OR 그룹 평가(evaluate_member)가 같은 판정을
    공유하도록 한 곳에 둔다.
    """
    if field_name != "income_status" or not is_income_status_denied(condition_value):
        return False
    return "none" not in {str(v).strip().lower() for v in rule_values(rule_value)}


def evaluate_member(
    rule: dict[str, Any],
    condition: dict[str, Any],
    condition_value_fn: ConditionValueFn,
    rule_matches_fn: RuleMatchesFn,
) -> tuple[str, Any, Any]:
    """OR 그룹의 단일 멤버를 평가한다. (verdict, condition_value, rule_value)."""
    field_name = str(rule.get("field_name") or "")
    condition_value = condition_value_fn(condition, field_name)
    rule_value = rule.get("value_json")
    operator = str(rule.get("operator") or "").upper()

    # 수급 자격 명시적 부정 → 수급-필요 정책은 manual_check/hard filter 여부와 무관하게
    # 확정 미충족(FAIL). manual_check보다 먼저 판정해 "수급 여부 확인"으로 새지 않게 한다.
    if is_denied_income_mismatch(field_name, condition_value, rule_value):
        return VERDICT_FAIL, condition_value, rule_value

    if rule.get("manual_check_required") is True:
        return VERDICT_MANUAL, condition_value, rule_value
    if condition_value in (None, "", []):
        # 폼이 빠짐없이 받는 특수상황(special)을 비워서 제출 = "해당 없음" 명시.
        # 미입력(불확정)이 아니라 확정 미일치로 본다. (예: "수급 OR 한부모" OR 그룹에서
        # 수급 부정 + 한부모 미선택 → 그룹 확정 탈락 → 제외)
        # condition_value 모듈이 본 모듈을 import하므로 순환 회피 위해 지연 import.
        from app.services.policy_rule_condition_value import is_exhaustive_empty

        if is_exhaustive_empty(condition, field_name):
            if rule.get("is_hard_filter") is True:
                return VERDICT_FAIL, condition_value, rule_value
            return VERDICT_SKIP, condition_value, rule_value
        if rule.get("is_hard_filter") is True:
            return VERDICT_MISSING, condition_value, rule_value
        # soft 조건 + 입력 없음 = 불확정(평가 불가). soft 미일치(SKIP)와 구분한다.
        return VERDICT_INDETERMINATE, condition_value, rule_value

    matched = rule_matches_fn(operator, condition_value, rule_value)
    if matched is True:
        return VERDICT_MATCH, condition_value, rule_value
    if matched is None:
        return VERDICT_MANUAL, condition_value, rule_value
    if rule.get("is_hard_filter") is True:
        return VERDICT_FAIL, condition_value, rule_value
    return VERDICT_SKIP, condition_value, rule_value


def evaluate_or_group(
    group_rules: list[dict[str, Any]],
    condition: dict[str, Any],
    condition_value_fn: ConditionValueFn,
    rule_matches_fn: RuleMatchesFn,
) -> tuple[str, list[tuple[dict[str, Any], str, Any, Any]]]:
    """OR 그룹을 종합 평가한다.

    - 하나라도 match → match (다른 대안의 미평가는 무시)
    - match는 없지만 manual/missing 존재 → manual(확정 불가)
    - 확정 실패(fail)가 있으나, 평가 불가한 대안(indeterminate)도 있으면 → manual
      (hard 대안은 실패했지만 입력 없는 soft 대안이 충족됐을 수도 있으므로 확정 탈락 금지)
    - 모든 대안이 확정 실패(fail)뿐 → fail
    - 그 외(soft 미일치/입력없음만) → skip(무시)
    반환: (outcome, [(rule, verdict, condition_value, rule_value), ...])
    """
    verdicts: list[tuple[dict[str, Any], str, Any, Any]] = []
    statuses: set[str] = set()
    for rule in group_rules:
        verdict, condition_value, rule_value = evaluate_member(
            rule, condition, condition_value_fn, rule_matches_fn
        )
        verdicts.append((rule, verdict, condition_value, rule_value))
        statuses.add(verdict)

    if VERDICT_MATCH in statuses:
        outcome = OUTCOME_MATCH
    elif statuses & {VERDICT_MANUAL, VERDICT_MISSING}:
        outcome = OUTCOME_MANUAL
    elif VERDICT_FAIL in statuses:
        # 확정 실패가 있어도, 입력이 없어 평가 못 한 대안이 함께 있으면 확정 탈락하지 않는다.
        outcome = (
            OUTCOME_MANUAL if VERDICT_INDETERMINATE in statuses else OUTCOME_FAIL
        )
    else:
        outcome = OUTCOME_SKIP
    return outcome, verdicts


def group_note(group_rules: list[dict[str, Any]], fallback: str) -> str:
    """OR 그룹 멤버들의 note를 ' 또는 '으로 합쳐 사람이 읽을 사유를 만든다."""
    notes: list[str] = []
    for rule in group_rules:
        note = rule.get("note") or rule.get("field_name")
        if note:
            notes.append(str(note))
    return " 또는 ".join(dict.fromkeys(notes)) if notes else fallback

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
        return str(condition_value) == str(values[0])
    if operator == "IN":
        targets = {str(value) for value in values}
        if isinstance(condition_value, list):
            return bool({str(item) for item in condition_value} & targets)
        return str(condition_value) in targets
    if operator in {"GTE", "LTE", "GT", "LT"}:
        rule_number = to_number(values[0] if values else None)
        if rule_number is None:
            return None
        # 사용자 income은 구간(버킷)이라 점 비교 대신 범위 비교한다. 기준이 구간을
        # 가로지르면 None(수동 확인) → 저소득층이 32% 같은 기준에서 통째로 빠지지 않게.
        bucket = INCOME_LEVEL_RANGE.get(str(condition_value).strip())
        if bucket is not None:
            return _income_bucket_match(operator, bucket[0], bucket[1], rule_number)
        condition_number = to_number(condition_value)
        if condition_number is None:
            return None
        if operator == "GTE":
            return condition_number >= rule_number
        if operator == "LTE":
            return condition_number <= rule_number
        if operator == "GT":
            return condition_number > rule_number
        return condition_number < rule_number
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

    if rule.get("manual_check_required") is True:
        return VERDICT_MANUAL, condition_value, rule_value
    if condition_value in (None, "", []):
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

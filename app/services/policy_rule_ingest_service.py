import logging
from typing import Annotated, Any

from fastapi import Depends

from app.common.psycopg_pool_conf import psycopg_pool
from app.repositories.policy_rule_ingest_repository import (
    PolicyRuleIngestRepository,
)
from app.schemas.policy_rule_ingest_schema import (
    PolicyRuleIngestItem,
    PolicyRuleIngestResponse,
    PolicyRuleIngestSkipItem,
)


# condition_json 값 → policy_types.py enum 정규화.
# #157에서 SQL REPLACE로 이미 정규화했더라도, 평탄화 단계의 안전망으로 한 번 더 매핑한다.
_STAGE_VALUE_MAP = {
    "youth": "teen",  # 청소년
}
_SPECIAL_CONDITION_VALUE_MAP = {
    "disabled_household": "disabled",
    "single_parent_or_grandparent": "single",
    "multichild": "many",
    "multicultural_or_defector": "multi",
}

# condition_json field_name → 두 필터(_condition_value)가 사용자 조건에서 해석하는 key.
# 핵심 의미가 일치하는 경우만 rename한다.
#   special_condition     → special (사용자 special 플래그)
#   median_income_percent → income  (사용자 income 구간 %와 LTE/GTE 비교)
# income_status(수급자격)는 income(중위소득 구간)과 축이 달라 rename하지 않는다.
_FIELD_RENAME = {
    "special_condition": "special",
    "median_income_percent": "income",
}

# 두 필터가 사용자 입력에서 해석 가능한 field. 여기에 없는 hard 조건은 매칭이 불가하므로
# manual_check로 강등해, 모든 사용자에게 "조건 없음/실패"로 빠지는 노이즈를 막는다.
# (income_status·household_member_age·age 등은 대응 사용자 입력 필드가 아직 없음)
_FILTER_RESOLVABLE_FIELDS = {
    "stage",
    "child_age",
    "income",
    "special",
    "region",
    "household_type",
    "pregnancy_status",
}

# matching_strength → policy_rule 저장 속성.
_STRENGTH_HARD = "hard"
_STRENGTH_SOFT = "soft"
_STRENGTH_FOLLOW_UP = "follow_up"

# confidence가 이 값보다 낮으면 수동 확인 대상으로 강등한다.
_LOW_CONFIDENCE_THRESHOLD = 0.5

# tree leaf가 아닌 보존 버킷의 rule_group / group_operator.
_GROUP_EXCLUSION = "EXCLUSION"
_GROUP_UNKNOWN = "UNKNOWN"
_GROUP_UNSUPPORTED = "UNSUPPORTED"
_ROOT_GROUP = "ALL"

# profile 파생 rule을 OpenAPI SQL 추출 rule과 구분하는 origin 값.
ORIGIN_CONDITION_PROFILE = "condition_profile"

_REASON_SOURCE_NOT_FOUND = "source_text가 원문에서 확인되지 않음(환각 가능성)"
_REASON_LOW_CONFIDENCE = "신뢰도가 낮아 수동 확인 필요"
_REASON_NO_STRENGTH = "matching_strength 미상 - 수동 확인 필요"
_REASON_UNRESOLVABLE_FIELD = "사용자 입력에 대응 field가 없어 자동 매칭 불가 - 수동 확인 필요"


class PolicyRuleIngestService:
    """policy_condition_profile.condition_json을 policy_rule row로 파생 저장한다.

    - condition_tree의 AND/OR 구조를 rule_group(group_key) / group_operator(AND·OR)로 보존
    - exclusions는 is_exclusion=true
    - unknowns / unsupported_conditions / 낮은 confidence / 검증 실패 source_text는
      manual_check_required=true, review_required=true로 보존
    - 값 어휘는 policy_types.py enum으로 정규화(youth→teen, disabled_household→disabled 등)
    - source_text가 원문에 실제 존재하는지 검증(환각 방지)
    """

    def __init__(
        self,
        repository: type[PolicyRuleIngestRepository]
        | PolicyRuleIngestRepository = PolicyRuleIngestRepository,
    ) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyRuleIngestService")
        self.repository = repository

    async def ingest_rules(
        self,
        limit: int = 10,
        overwrite: bool = False,
    ) -> PolicyRuleIngestResponse:
        items: list[PolicyRuleIngestItem] = []
        skipped: list[PolicyRuleIngestSkipItem] = []
        failed: list[dict[str, str]] = []

        async with psycopg_pool.connection() as conn:
            await self.repository.ensure_schema(conn)
            targets = await self.repository.find_rule_targets(
                conn=conn,
                limit=limit,
                overwrite=overwrite,
            )

            for target in targets:
                try:
                    rules = self.build_rules(
                        condition_json=target.get("condition_json") or {},
                        profile_source_text=target.get("source_text"),
                        profile_confidence=target.get("confidence"),
                        profile_review_required=bool(target.get("review_required")),
                    )
                    if not rules:
                        skipped.append(
                            PolicyRuleIngestSkipItem(
                                policy_id=target["policy_id"],
                                policy_code=target["policy_code"],
                                policy_name=target["policy_name"],
                                reason="condition_json에서 파생할 rule이 없습니다.",
                            )
                        )
                        continue

                    async with conn.transaction():
                        inserted = await self.repository.replace_rules_for_policy(
                            conn=conn,
                            policy_id=target["policy_id"],
                            rules=rules,
                        )

                    items.append(
                        PolicyRuleIngestItem(
                            policy_id=target["policy_id"],
                            policy_code=target["policy_code"],
                            policy_name=target["policy_name"],
                            rule_count=inserted,
                            hard_rule_count=sum(
                                1
                                for rule in rules
                                if rule["is_hard_filter"] and not rule["is_exclusion"]
                            ),
                            exclusion_count=sum(
                                1 for rule in rules if rule["is_exclusion"]
                            ),
                            manual_check_count=sum(
                                1 for rule in rules if rule["manual_check_required"]
                            ),
                        )
                    )
                except Exception as exc:
                    self.logger.exception("policy_rule 파생 저장 중 오류 발생")
                    failed.append(
                        {
                            "policy_id": str(target.get("policy_id")),
                            "policy_code": str(target.get("policy_code")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return PolicyRuleIngestResponse(
            requested_count=len(targets),
            completed_count=len(items),
            skipped_count=len(skipped),
            failed_count=len(failed),
            items=items,
            skipped=skipped,
            failed=failed,
        )

    # ------------------------------------------------------------------
    # 평탄화(순수 함수) - DB 없이 단위 테스트 가능
    # ------------------------------------------------------------------

    def build_rules(
        self,
        *,
        condition_json: dict[str, Any],
        profile_source_text: str | None,
        profile_confidence: float | None,
        profile_review_required: bool,
    ) -> list[dict[str, Any]]:
        ctx = _BuildContext(
            profile_source_text=profile_source_text,
            profile_confidence=profile_confidence,
            profile_review_required=profile_review_required,
            normalized_raw=self._normalize_whitespace(profile_source_text or ""),
        )
        rules: list[dict[str, Any]] = []

        tree = condition_json.get("condition_tree") or {}
        self._walk(tree, _ROOT_GROUP, "AND", rules, ctx)

        for exclusion in condition_json.get("exclusions") or []:
            rule = self._exclusion_rule(exclusion, ctx)
            if rule is not None:
                rules.append(rule)

        for item in condition_json.get("unknowns") or []:
            rules.append(self._manual_rule(item, _GROUP_UNKNOWN, ctx))
        for item in condition_json.get("unsupported_conditions") or []:
            rules.append(self._manual_rule(item, _GROUP_UNSUPPORTED, ctx))

        return rules

    def _walk(
        self,
        node: Any,
        group_key: str,
        group_operator: str,
        rules: list[dict[str, Any]],
        ctx: "_BuildContext",
    ) -> None:
        if not isinstance(node, dict):
            return

        conditions = node.get("conditions")
        if isinstance(conditions, list):
            operator = str(node.get("operator") or "AND").strip().upper()
            if operator not in ("AND", "OR"):
                operator = "AND"
            # 그룹 노드는 자신의 group_key(없으면 상위 group_key 상속)와 operator를
            # 자식에게 물려준다. 그래야 leaf가 자신이 속한 그룹의 키/연산자를 갖는다.
            child_group_key = str(node.get("group_key") or "").strip() or group_key
            for child in conditions:
                self._walk(child, child_group_key, operator, rules, ctx)
            return

        rule = self._leaf_rule(node, group_key, group_operator, ctx)
        if rule is not None:
            rules.append(rule)

    def _leaf_rule(
        self,
        leaf: dict[str, Any],
        group_key: str,
        group_operator: str,
        ctx: "_BuildContext",
    ) -> dict[str, Any] | None:
        raw_field = self._field_name(leaf.get("field"))
        if not raw_field:
            return None

        operator = str(leaf.get("operator") or "UNKNOWN").strip().upper() or "UNKNOWN"
        field_name, value, operator = self._map_field(
            raw_field, leaf.get("value"), operator
        )
        strength = str(leaf.get("matching_strength") or "").strip().lower()
        confidence = self._confidence(leaf.get("confidence"), ctx)
        source_text = leaf.get("source_text")

        is_hard = strength == _STRENGTH_HARD
        manual = False
        review = ctx.profile_review_required
        reasons: list[str] = []

        if strength == _STRENGTH_FOLLOW_UP:
            manual = True
            review = True
        elif strength not in (_STRENGTH_HARD, _STRENGTH_SOFT):
            manual = True
            review = True
            reasons.append(_REASON_NO_STRENGTH)

        if confidence is not None and confidence < _LOW_CONFIDENCE_THRESHOLD:
            manual = True
            review = True
            reasons.append(_REASON_LOW_CONFIDENCE)

        if not self._source_present(source_text, ctx):
            manual = True
            review = True
            reasons.append(_REASON_SOURCE_NOT_FOUND)

        # 두 필터가 해석할 수 없는 hard field는 자동 매칭이 불가하므로 manual로 강등한다.
        if is_hard and field_name not in _FILTER_RESOLVABLE_FIELDS:
            manual = True
            review = True
            reasons.append(_REASON_UNRESOLVABLE_FIELD)

        return self._rule(
            rule_type=self._rule_type(leaf.get("type"), field_name),
            operator=operator,
            field_name=field_name,
            value=value,
            is_hard_filter=is_hard and not manual,
            manual_check_required=manual,
            manual_check_reason="; ".join(reasons) or (source_text if manual else None),
            note=group_key if group_key != _ROOT_GROUP else field_name,
            rule_group=group_key,
            group_operator=group_operator,
            source_text=source_text,
            confidence=confidence,
            review_required=review,
            is_exclusion=False,
        )

    def _exclusion_rule(
        self,
        exclusion: dict[str, Any],
        ctx: "_BuildContext",
    ) -> dict[str, Any] | None:
        raw_field = self._field_name(exclusion.get("field")) or "exclusion"
        operator = str(exclusion.get("operator") or "EQ").strip().upper() or "EQ"
        field_name, value, operator = self._map_field(
            raw_field, exclusion.get("value"), operator
        )
        source_text = exclusion.get("source_text")
        # 제외 조건은 hard로 자동 탈락시키지 않고 수동 확인 대상으로 보존한다.
        return self._rule(
            rule_type="EXCLUSION",
            operator=operator,
            field_name=field_name,
            value=value,
            is_hard_filter=False,
            manual_check_required=True,
            manual_check_reason=str(exclusion.get("reason") or source_text or "제외 조건"),
            note=str(exclusion.get("reason") or "exclusion"),
            rule_group=_GROUP_EXCLUSION,
            group_operator="AND",
            source_text=source_text,
            confidence=self._confidence(exclusion.get("confidence"), ctx),
            review_required=True,
            is_exclusion=True,
        )

    def _manual_rule(
        self,
        item: dict[str, Any],
        group_key: str,
        ctx: "_BuildContext",
    ) -> dict[str, Any]:
        raw_field = self._field_name(item.get("field")) or group_key.lower()
        field_name, value, _ = self._map_field(raw_field, item.get("value"), "UNKNOWN")
        return self._rule(
            rule_type=group_key,
            operator="UNKNOWN",
            field_name=field_name,
            value=value,
            is_hard_filter=False,
            manual_check_required=True,
            manual_check_reason=str(
                item.get("reason") or item.get("source_text") or group_key
            ),
            note=str(item.get("reason") or group_key),
            rule_group=group_key,
            group_operator="AND",
            source_text=item.get("source_text"),
            confidence=self._confidence(item.get("confidence"), ctx),
            review_required=True,
            is_exclusion=False,
        )

    def _rule(
        self,
        *,
        rule_type: str,
        operator: str,
        field_name: str,
        value: Any,
        is_hard_filter: bool,
        manual_check_required: bool,
        manual_check_reason: str | None,
        note: str | None,
        rule_group: str,
        group_operator: str,
        source_text: Any,
        confidence: float | None,
        review_required: bool,
        is_exclusion: bool,
    ) -> dict[str, Any]:
        return {
            "rule_type": rule_type,
            "operator": operator,
            "field_name": field_name,
            "value_json": {} if value is None else value,
            "is_hard_filter": is_hard_filter,
            "manual_check_required": manual_check_required,
            "manual_check_reason": manual_check_reason,
            "note": note,
            "rule_group": rule_group,
            "group_operator": group_operator,
            "source_text": str(source_text) if source_text is not None else None,
            "confidence": confidence,
            "review_required": review_required,
            "is_exclusion": is_exclusion,
        }

    # ------------------------------------------------------------------
    # 보조 헬퍼
    # ------------------------------------------------------------------

    def _field_name(self, field: Any) -> str | None:
        if field is None:
            return None
        text = str(field).strip().lower()
        return text or None

    def _rule_type(self, rule_type: Any, field_name: str) -> str:
        text = str(rule_type or "").strip()
        return (text or field_name).upper()[:50]

    def _map_field(
        self, raw_field: str, value: Any, operator: str
    ) -> tuple[str, Any, str]:
        """condition_json field → 필터 key로 정규화하고 값/operator도 함께 보정한다.

        operator를 함께 반환하는 이유: 사용자 special 값은 리스트(["disabled"])이므로
        rule도 IN + 리스트 값이어야 필터의 포함 비교가 성립한다(EQ면 매칭 실패).
        """
        if raw_field == "stage":
            return "stage", self._map_value(_STAGE_VALUE_MAP, value), operator
        if raw_field == "special_condition":
            mapped = self._map_value(_SPECIAL_CONDITION_VALUE_MAP, value)
            as_list = mapped if isinstance(mapped, list) else [mapped]
            return "special", as_list, "IN"
        if raw_field == "median_income_percent":
            # income 구간(%)과 비교 가능하도록 {"percent": n} → 숫자로 평탄화.
            return "income", self._percent_number(value), operator
        return _FIELD_RENAME.get(raw_field, raw_field), value, operator

    def _map_value(self, mapping: dict[str, str], value: Any) -> Any:
        if isinstance(value, list):
            return [mapping.get(str(v), v) for v in value]
        return mapping.get(str(value), value)

    def _percent_number(self, value: Any) -> Any:
        if isinstance(value, dict):
            for key in ("percent", "median_income_percent", "value"):
                if value.get(key) is not None:
                    value = value[key]
                    break
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value.replace("%", "").strip()))
            except ValueError:
                return value
        return value

    def _confidence(self, value: Any, ctx: "_BuildContext") -> float | None:
        if value is None:
            return ctx.profile_confidence
        try:
            return float(value)
        except (TypeError, ValueError):
            return ctx.profile_confidence

    def _source_present(self, snippet: Any, ctx: "_BuildContext") -> bool:
        if snippet is None:
            return True
        text = self._normalize_whitespace(str(snippet))
        if not text:
            return True
        if not ctx.normalized_raw:
            # 원문이 없으면 검증 불가 - 통과 처리(과도한 강등 방지).
            return True
        return text in ctx.normalized_raw

    def _normalize_whitespace(self, text: str) -> str:
        return "".join(text.split())


class _BuildContext:
    __slots__ = (
        "profile_source_text",
        "profile_confidence",
        "profile_review_required",
        "normalized_raw",
    )

    def __init__(
        self,
        *,
        profile_source_text: str | None,
        profile_confidence: float | None,
        profile_review_required: bool,
        normalized_raw: str,
    ) -> None:
        self.profile_source_text = profile_source_text
        self.profile_confidence = profile_confidence
        self.profile_review_required = profile_review_required
        self.normalized_raw = normalized_raw


def get_policy_rule_ingest_service() -> PolicyRuleIngestService:
    return PolicyRuleIngestService()


PolicyRuleIngestServiceDep = Annotated[
    PolicyRuleIngestService,
    Depends(get_policy_rule_ingest_service),
]

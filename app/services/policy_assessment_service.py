import logging
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import Depends

from app.common.ai_status import (
    AssessmentStatus,
    map_assessment_to_user_status,
)
from app.schemas.ai_contract import AssessmentInput, AssessmentResult, EvidenceChunk
from app.schemas.policy_rag_schema import PolicyRagSearchResult


ASSESSMENT_TYPE_RECOMMENDATION = "recommendation_assessment"
ASSESSMENT_TYPE_ELIGIBILITY = "eligibility_detail"


class PolicyAssessmentService:
    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyAssessmentService")

    def assess(
        self,
        assessment_input: AssessmentInput,
        assessment_type: str = ASSESSMENT_TYPE_RECOMMENDATION,
    ) -> list[AssessmentResult]:
        self._validate_assessment_type(assessment_type)
        policy_ids = self._policy_ids(assessment_input)
        return [
            self.assess_policy(
                assessment_input=assessment_input,
                policy_id=policy_id,
                assessment_type=assessment_type,
            )
            for policy_id in policy_ids
        ]

    def assess_policy(
        self,
        assessment_input: AssessmentInput,
        policy_id: int | str,
        assessment_type: str = ASSESSMENT_TYPE_RECOMMENDATION,
    ) -> AssessmentResult:
        self._validate_assessment_type(assessment_type)
        condition = assessment_input.merged_condition_json
        matched_conditions = self._string_list(
            condition,
            "matched_conditions",
            "passed_conditions",
            "satisfied_conditions",
        )
        missing_conditions = self._missing_conditions(condition)
        conflicting_conditions = self._conflicting_conditions(condition)
        manual_check_points = self._manual_check_points(condition)
        not_matched_conditions = self._string_list(
            condition,
            "not_matched_conditions",
            "failed_conditions",
            "mismatched_conditions",
            "rule_failures",
        )

        assessment_status = self._decide_status(
            missing_conditions=missing_conditions,
            conflicting_conditions=conflicting_conditions,
            not_matched_conditions=not_matched_conditions,
            manual_check_points=manual_check_points,
            condition=condition,
        )
        return AssessmentResult(
            policy_id=policy_id,
            assessment_status=assessment_status,
            user_status=map_assessment_to_user_status(assessment_status),
            reason_summary=self._reason_summary(
                assessment_status=assessment_status,
                matched_conditions=matched_conditions,
                missing_conditions=missing_conditions,
                conflicting_conditions=conflicting_conditions,
                not_matched_conditions=not_matched_conditions,
            ),
            matched_conditions=matched_conditions,
            missing_conditions=missing_conditions,
            conflicting_conditions=conflicting_conditions,
            manual_check_points=manual_check_points,
            evidences=self._evidences_for_policy(
                assessment_input.evidence_chunks,
                policy_id,
            ),
        )

    def build_evidence_chunks(
        self,
        search_results: list[PolicyRagSearchResult],
    ) -> list[EvidenceChunk]:
        return [
            EvidenceChunk(
                chunk_id=result.chunk_id or "",
                policy_id=result.policy_id or "",
                snippet=result.chunk_text,
                source_title=result.source_title or self._source_title(result),
                source_url=result.source_url or "",
                score=result.distance,
                evidence_role=result.evidence_role or self._evidence_role(result.section),
            )
            for result in search_results
        ]

    def _decide_status(
        self,
        missing_conditions: list[str],
        conflicting_conditions: list[str],
        not_matched_conditions: list[str],
        manual_check_points: list[str],
        condition: dict[str, Any],
    ) -> AssessmentStatus:
        if self._has_result_changing_conflict(condition, conflicting_conditions):
            return AssessmentStatus.CONFLICTING_PROFILE
        if len(missing_conditions) >= 2:
            return AssessmentStatus.INSUFFICIENT_PROFILE
        if not_matched_conditions:
            return AssessmentStatus.NOT_MATCH
        if missing_conditions or manual_check_points or self._ambiguous_conditions(condition):
            return AssessmentStatus.NEEDS_MORE_INFO
        return AssessmentStatus.LIKELY_MATCH

    def _missing_conditions(self, condition: dict[str, Any]) -> list[str]:
        missing = self._string_list(
            condition,
            "missing_conditions",
            "missing_fields",
            "core_missing_fields",
        )
        missing.extend(
            issue["field_name"]
            for issue in self._dict_list(condition.get("input_issues"))
            if issue.get("issue_type") == "missing" and issue.get("field_name")
        )
        return self._deduplicate(missing)

    def _conflicting_conditions(self, condition: dict[str, Any]) -> list[str]:
        conflicts = self._string_list(condition, "conflicting_conditions")
        conflicts.extend(
            conflict.get("field_name")
            for conflict in self._dict_list(condition.get("profile_conflicts"))
            if conflict.get("field_name")
        )
        return self._deduplicate(conflicts)

    def _manual_check_points(self, condition: dict[str, Any]) -> list[str]:
        manual_check_points = self._string_list(
            condition,
            "manual_check_points",
            "manual_checks",
        )
        manual_check_points.extend(self._ambiguous_conditions(condition))
        return self._deduplicate(manual_check_points)

    def _ambiguous_conditions(self, condition: dict[str, Any]) -> list[str]:
        ambiguous = self._string_list(
            condition,
            "ambiguous_conditions",
            "ambiguous_fields",
        )
        ambiguous.extend(
            str(key)
            for key, value in condition.items()
            if self._is_unknown_value(value)
        )
        ambiguous.extend(
            issue["field_name"]
            for issue in self._dict_list(condition.get("input_issues"))
            if issue.get("issue_type") == "ambiguous" and issue.get("field_name")
        )
        return self._deduplicate(ambiguous)

    def _is_unknown_value(self, value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() == "unknown"
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
            return any(self._is_unknown_value(item) for item in value)
        return False

    def _has_result_changing_conflict(
        self,
        condition: dict[str, Any],
        conflicting_conditions: list[str],
    ) -> bool:
        if not conflicting_conditions:
            return False
        if condition.get("rule_filter_result_diverged") is True:
            return True
        if len(condition.get("normalized_candidates") or []) >= 2:
            return True
        return any(
            conflict.get("changes_rule_filter_result") is True
            for conflict in self._dict_list(condition.get("profile_conflicts"))
        )

    def _reason_summary(
        self,
        assessment_status: AssessmentStatus,
        matched_conditions: list[str],
        missing_conditions: list[str],
        conflicting_conditions: list[str],
        not_matched_conditions: list[str],
    ) -> str:
        if assessment_status == AssessmentStatus.CONFLICTING_PROFILE:
            return "입력 충돌로 판정 기준이 갈립니다."
        if assessment_status == AssessmentStatus.INSUFFICIENT_PROFILE:
            return "핵심 조건이 2개 이상 부족해 판정 신뢰도가 낮습니다."
        if assessment_status == AssessmentStatus.NOT_MATCH:
            return f"핵심 조건이 맞지 않습니다: {', '.join(not_matched_conditions)}"
        if assessment_status == AssessmentStatus.NEEDS_MORE_INFO:
            return f"추가 확인이 필요합니다: {', '.join(missing_conditions) or '조건 모호'}"
        if matched_conditions:
            return f"현재 정보 기준으로 추천 가능성이 높습니다: {', '.join(matched_conditions)}"
        return "현재 정보 기준으로 추천 가능성이 높습니다."

    def _evidences_for_policy(
        self,
        evidences: list[EvidenceChunk],
        policy_id: int | str,
    ) -> list[EvidenceChunk]:
        return [
            evidence
            for evidence in evidences
            if str(evidence.policy_id) == str(policy_id)
        ]

    def _source_title(self, result: PolicyRagSearchResult) -> str:
        if result.policy_name and result.section:
            return f"{result.policy_name} - {result.section}"
        return result.policy_name or "정책 근거"

    def _evidence_role(self, section: str | None) -> str | None:
        if section is None:
            return None
        role_by_section = {
            "기본 정보": "SUMMARY",
            "요약": "SUMMARY",
            "지원 대상": "TARGET",
            "지원 내용": "BENEFIT",
            "신청 방법": "APPLICATION",
            "신청 기간": "APPLICATION",
            "유의 사항": "CAUTION",
        }
        return role_by_section.get(section)

    def _policy_ids(self, assessment_input: AssessmentInput) -> list[int | str]:
        if assessment_input.policy_id is not None:
            return [assessment_input.policy_id]
        return list(assessment_input.policy_ids or [])

    def _validate_assessment_type(self, assessment_type: str) -> None:
        if assessment_type not in {
            ASSESSMENT_TYPE_RECOMMENDATION,
            ASSESSMENT_TYPE_ELIGIBILITY,
        }:
            raise ValueError(f"지원하지 않는 assessment_type입니다: {assessment_type}")

    def _string_list(self, condition: dict[str, Any], *keys: str) -> list[str]:
        values: list[str] = []
        for key in keys:
            values.extend(self._coerce_string_list(condition.get(key)))
        return self._deduplicate(values)

    def _coerce_string_list(self, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [
                str(item)
                for item in value.values()
                if item is not None and str(item).strip()
            ]
        if isinstance(value, Iterable):
            return [
                str(item)
                for item in value
                if item is not None and str(item).strip()
            ]
        return [str(value)]

    def _dict_list(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _deduplicate(self, values: Iterable[str | None]) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value is None:
                continue
            normalized = str(value).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduplicated.append(normalized)
        return deduplicated


PolicyAssessmentServiceDep = Annotated[
    PolicyAssessmentService,
    Depends(PolicyAssessmentService),
]

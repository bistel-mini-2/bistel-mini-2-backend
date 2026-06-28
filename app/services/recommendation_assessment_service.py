from dataclasses import dataclass
from typing import Any

from app.common.ai_status import (
    AssessmentStatus,
    UserStatus,
    map_assessment_to_user_status,
)
from app.services.recommendation_candidate_service import (
    CANDIDATE_STATUS_CANDIDATE,
    CANDIDATE_STATUS_EXCLUDED,
    CANDIDATE_STATUS_UNCERTAIN,
    FIELD_DOMAIN,
    PolicyCandidate,
)


@dataclass
class RecommendationPolicyAssessment:
    policy_id: int
    assessment_status: AssessmentStatus
    user_status: UserStatus
    confidence_score: float
    matched_conditions_json: list[dict[str, Any]]
    missing_conditions_json: list[dict[str, Any]]
    conflicting_conditions_json: list[dict[str, Any]]
    manual_check_points_json: list[dict[str, Any]]
    reason_summary: str
    selected_for_result: bool = False


class RecommendationAssessmentService:
    async def assess_candidates(
        self,
        merged_condition_json: dict[str, Any],
        candidates: list[PolicyCandidate],
        input_issues: list[dict[str, Any]] | None = None,
        profile_conflict_json: list[dict[str, Any]] | None = None,
        result_limit: int = 6,
    ) -> list[RecommendationPolicyAssessment]:
        assessments = [
            self._assess_candidate(
                candidate=candidate,
                merged_condition_json=merged_condition_json,
                input_issues=input_issues or [],
                profile_conflict_json=profile_conflict_json or [],
            )
            for candidate in candidates
        ]
        candidate_by_policy = {
            int(candidate.policy.policy_id): candidate
            for candidate in candidates
        }
        selected_policy_ids = {
            assessment.policy_id
            for assessment in sorted(
                (
                    assessment
                    for assessment in assessments
                    if assessment.assessment_status != AssessmentStatus.NOT_MATCH
                ),
                key=lambda assessment: self._selection_key(
                    assessment,
                    candidate_by_policy[assessment.policy_id],
                ),
            )[:result_limit]
        }
        for assessment in assessments:
            assessment.selected_for_result = assessment.policy_id in selected_policy_ids
        return assessments

    def _assess_candidate(
        self,
        candidate: PolicyCandidate,
        merged_condition_json: dict[str, Any],
        input_issues: list[dict[str, Any]],
        profile_conflict_json: list[dict[str, Any]],
    ) -> RecommendationPolicyAssessment:
        filter_match_json = candidate.filter_match_json or {}
        matched_conditions = self._dict_list(filter_match_json.get("matched_rules"))
        uncertain_rules = self._dict_list(filter_match_json.get("uncertain_rules"))
        excluded_rules = self._dict_list(filter_match_json.get("excluded_rules"))
        if candidate.candidate_status == CANDIDATE_STATUS_EXCLUDED:
            return self._excluded_assessment(candidate, excluded_rules)

        missing_conditions = self._missing_conditions(
            merged_condition_json=merged_condition_json,
            input_issues=input_issues,
            uncertain_rules=uncertain_rules,
            candidate=candidate,
        )
        manual_check_points = self._manual_check_points(uncertain_rules)
        conflicting_conditions = self._dict_list(profile_conflict_json)
        assessment_status = self._assessment_status(
            candidate=candidate,
            missing_conditions=missing_conditions,
            conflicting_conditions=conflicting_conditions,
        )
        return RecommendationPolicyAssessment(
            policy_id=int(candidate.policy.policy_id),
            assessment_status=assessment_status,
            user_status=map_assessment_to_user_status(assessment_status),
            confidence_score=self._confidence_score(
                assessment_status,
                candidate.retrieval_score,
            ),
            matched_conditions_json=matched_conditions,
            missing_conditions_json=missing_conditions,
            conflicting_conditions_json=conflicting_conditions,
            manual_check_points_json=manual_check_points,
            reason_summary=self._reason_summary(
                assessment_status=assessment_status,
                matched_conditions=matched_conditions,
                missing_conditions=missing_conditions,
                excluded_rules=excluded_rules,
                manual_check_points=manual_check_points,
            ),
        )

    def _excluded_assessment(
        self,
        candidate: PolicyCandidate,
        excluded_rules: list[dict[str, Any]],
    ) -> RecommendationPolicyAssessment:
        return RecommendationPolicyAssessment(
            policy_id=int(candidate.policy.policy_id),
            assessment_status=AssessmentStatus.NOT_MATCH,
            user_status=map_assessment_to_user_status(AssessmentStatus.NOT_MATCH),
            confidence_score=self._confidence_score(
                AssessmentStatus.NOT_MATCH,
                candidate.retrieval_score,
            ),
            matched_conditions_json=[],
            missing_conditions_json=[],
            conflicting_conditions_json=[],
            manual_check_points_json=[],
            reason_summary=self._reason_summary(
                assessment_status=AssessmentStatus.NOT_MATCH,
                matched_conditions=[],
                missing_conditions=[],
                excluded_rules=excluded_rules,
                manual_check_points=[],
            ),
        )

    def _assessment_status(
        self,
        candidate: PolicyCandidate,
        missing_conditions: list[dict[str, Any]],
        conflicting_conditions: list[dict[str, Any]],
    ) -> AssessmentStatus:
        if self._has_result_changing_conflict(conflicting_conditions):
            return AssessmentStatus.CONFLICTING_PROFILE
        if self._missing_field_count(missing_conditions) >= 2:
            return AssessmentStatus.INSUFFICIENT_PROFILE
        if candidate.candidate_status == CANDIDATE_STATUS_UNCERTAIN:
            return AssessmentStatus.NEEDS_MORE_INFO
        if (
            candidate.candidate_status == CANDIDATE_STATUS_CANDIDATE
            and candidate.retrieval_score >= 0.5
        ):
            return AssessmentStatus.LIKELY_MATCH
        return AssessmentStatus.NEEDS_MORE_INFO

    def _missing_conditions(
        self,
        merged_condition_json: dict[str, Any],
        input_issues: list[dict[str, Any]],
        uncertain_rules: list[dict[str, Any]],
        candidate: PolicyCandidate,
    ) -> list[dict[str, Any]]:
        missing: list[dict[str, Any]] = []
        for field_name in self._missing_core_fields(merged_condition_json, candidate):
            missing.append(
                {
                    "field": field_name,
                    "reason": "핵심 사용자 조건이 부족합니다.",
                    "source": "merged_condition_json",
                }
            )
        for issue in input_issues:
            if issue.get("issue_type") == "missing":
                missing.append(
                    {
                        "field": issue.get("field_name"),
                        "reason": issue.get("message") or "입력값이 부족합니다.",
                        "source": "input_issues",
                    }
                )
        for rule in uncertain_rules:
            condition_value = rule.get("condition_value")
            if condition_value in (None, "", [], "unknown"):
                missing.append(
                    {
                        "field": rule.get("field"),
                        "reason": rule.get("reason") or "추가 조건 확인이 필요합니다.",
                        "source": "uncertain_rules",
                    }
                )
        return self._deduplicate_dicts(missing)

    def _missing_core_fields(
        self,
        merged_condition_json: dict[str, Any],
        candidate: PolicyCandidate,
    ) -> list[str]:
        # region/income은 정책이 실제로 그 도메인을 다룰 때만 결측 패널티를 준다.
        # 전국 정책에 "지역 결측", 비소득 정책에 "소득 결측"이 잘못 잡혀
        # 거의 모든 결과가 INSUFFICIENT_PROFILE(확인해 볼 정책)로 쏠리던 문제를 막는다.
        relevant_domains = self._policy_relevant_domains(candidate)
        missing: list[str] = []
        if "region" in relevant_domains and not self._first(
            merged_condition_json, "region", "region_code"
        ):
            missing.append("region")
        # 생애주기/자녀 나이는 가족·육아 복지 서비스의 핵심 식별자라 항상 확인한다.
        if not self._first(
            merged_condition_json,
            "stage",
            "life_stage",
            "target_stage",
            "childAge",
            "child_age",
            "child_age_range",
        ):
            missing.append("stage_or_childAge")
        # income domain 입력은 income/income_level 외에 income_status/benefit_status
        # /income_bracket 등 다양한 alias로 들어올 수 있어 모두 확인한다.
        if "income" in relevant_domains and not self._first(
            merged_condition_json,
            "income",
            "income_level",
            "income_bracket",
            "income_status",
            "benefit_status",
        ):
            missing.append("income")
        return missing

    def _policy_relevant_domains(self, candidate: PolicyCandidate) -> set[str]:
        """정책 rule이 실제로 다루는 semantic domain 집합(stage/income/region 등).

        filter_match_json의 matched/uncertain/excluded rule field를 domain으로 매핑한다.
        rule이 없으면 빈 집합이 되어 region/income 결측 패널티가 적용되지 않는다.
        """
        filter_match_json = candidate.filter_match_json or {}
        domains: set[str] = set()
        for key in ("matched_rules", "uncertain_rules", "excluded_rules"):
            for rule in self._dict_list(filter_match_json.get(key)):
                field = str(rule.get("field") or rule.get("field_name") or "")
                # 전국 정책은 지역과 무관한데 _apply_region_rule이
                # policy_value="NATIONAL" region matched rule을 넣는다.
                # 이를 region 관련 도메인으로 오인해 지역 결측 패널티를 주지 않는다.
                if (
                    field == "region"
                    and str(rule.get("policy_value") or "").upper() == "NATIONAL"
                ):
                    continue
                domain = FIELD_DOMAIN.get(field)
                if domain:
                    domains.add(domain)
        return domains

    def _missing_field_count(
        self,
        missing_conditions: list[dict[str, Any]],
    ) -> int:
        return len(
            {
                self._normalized_field_name(
                    item.get("field") or item.get("field_name")
                )
                for item in missing_conditions
                if item.get("field") or item.get("field_name")
            }
        )

    def _normalized_field_name(self, field_name: Any) -> str:
        field = str(field_name or "").strip()
        aliases = {
            "region_code": "region",
            "life_stage": "stage_or_childAge",
            "target_stage": "stage_or_childAge",
            "stage": "stage_or_childAge",
            "childAge": "stage_or_childAge",
            "child_age": "stage_or_childAge",
            "child_age_range": "stage_or_childAge",
            "income_level": "income",
            "income_bracket": "income",
            "income_status": "income",
            "benefit_status": "income",
            "median_income_percent": "income",
        }
        return aliases.get(field, field)

    def _manual_check_points(
        self,
        uncertain_rules: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return self._deduplicate_dicts(
            [
                {
                    "field": rule.get("field"),
                    "condition_value": rule.get("condition_value"),
                    "policy_value": rule.get("policy_value"),
                    "reason": rule.get("reason") or "수동 확인이 필요합니다.",
                    "source": "uncertain_rules",
                }
                for rule in uncertain_rules
            ]
        )

    def _has_result_changing_conflict(
        self,
        conflicting_conditions: list[dict[str, Any]],
    ) -> bool:
        return any(
            conflict.get("changes_rule_filter_result") is True
            for conflict in conflicting_conditions
        )

    def _confidence_score(
        self,
        assessment_status: AssessmentStatus,
        retrieval_score: float,
    ) -> float:
        if assessment_status == AssessmentStatus.LIKELY_MATCH:
            return round(min(max(retrieval_score, 0.7), 0.95), 2)
        if assessment_status == AssessmentStatus.NOT_MATCH:
            return round(min(retrieval_score, 0.3), 2)
        if assessment_status == AssessmentStatus.INSUFFICIENT_PROFILE:
            return 0.35
        if assessment_status == AssessmentStatus.CONFLICTING_PROFILE:
            return 0.4
        return round(min(max(retrieval_score, 0.45), 0.69), 2)

    def _reason_summary(
        self,
        assessment_status: AssessmentStatus,
        matched_conditions: list[dict[str, Any]],
        missing_conditions: list[dict[str, Any]],
        excluded_rules: list[dict[str, Any]],
        manual_check_points: list[dict[str, Any]],
    ) -> str:
        if assessment_status == AssessmentStatus.NOT_MATCH:
            reason = self._first_reason(excluded_rules)
            return reason or "핵심 조건이 맞지 않아 추천하기 어렵습니다."
        if assessment_status == AssessmentStatus.INSUFFICIENT_PROFILE:
            return "핵심 조건 정보가 부족해 추가 확인이 필요합니다."
        if assessment_status == AssessmentStatus.CONFLICTING_PROFILE:
            return "입력 조건과 저장된 프로필에 충돌이 있어 추가 확인이 필요합니다."
        if assessment_status == AssessmentStatus.NEEDS_MORE_INFO:
            reason = self._first_reason(missing_conditions) or self._first_reason(
                manual_check_points
            )
            return reason or "일부 조건은 추가 확인이 필요하지만 현재 조건 기준으로 후보로 유지됩니다."
        if matched_conditions:
            labels = self._matched_field_labels(matched_conditions)
            if labels:
                return f"{', '.join(labels)} 조건이 정책 기준과 맞아 추천 가능성이 높습니다."
            return "사용자 조건과 주요 정책 조건이 일치해 추천 가능성이 높습니다."
        return "현재 정보 기준으로 추천 가능성이 높습니다."

    _FIELD_LABELS = {
        "stage": "생애주기",
        "life_stage": "생애주기",
        "target_stage": "생애주기",
        "childAge": "자녀 연령",
        "child_age": "자녀 연령",
        "child_age_range": "자녀 연령",
        "region": "거주 지역",
        "region_code": "거주 지역",
        "income": "소득 구간",
        "income_level": "소득 구간",
        "income_bracket": "소득 구간",
        "special": "가구 특성",
        "special_flags": "가구 특성",
        "needs": "관심 지원",
    }

    def _matched_field_labels(
        self,
        matched_conditions: list[dict[str, Any]],
    ) -> list[str]:
        # candidate_search 는 내부 검색 신호라 사용자 노출 문구에서는 제외한다.
        labels: list[str] = []
        for condition in matched_conditions:
            field_name = str(condition.get("field") or condition.get("field_name") or "")
            label = self._FIELD_LABELS.get(field_name)
            if label and label not in labels:
                labels.append(label)
            if len(labels) >= 3:
                break
        return labels

    def _selection_key(
        self,
        assessment: RecommendationPolicyAssessment,
        candidate: PolicyCandidate,
    ) -> tuple[int, float]:
        priority_by_status = {
            UserStatus.RECOMMENDABLE: 0,
            UserStatus.NEEDS_CONFIRMATION: 1,
            UserStatus.DIFFICULT_TO_RECOMMEND: 2,
        }
        return (
            priority_by_status[assessment.user_status],
            -candidate.retrieval_score,
        )

    def _first(self, source: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = source.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _first_reason(self, rows: list[dict[str, Any]]) -> str | None:
        for row in rows:
            reason = row.get("reason")
            if reason:
                return str(reason)
        return None

    def _dict_list(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, dict)]

    def _deduplicate_dicts(
        self,
        values: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        deduplicated: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for value in values:
            field_name = str(value.get("field") or value.get("field_name") or "")
            reason = str(value.get("reason") or "")
            source = str(value.get("source") or "")
            key = (field_name, reason, source)
            if not field_name or key in seen:
                continue
            seen.add(key)
            deduplicated.append(value)
        return deduplicated

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
from app.common.policy_types import LIFE_STAGE_TO_DB
from app.schemas.ai_contract import EvidenceChunk
from app.services.recommendation_candidate_service import (
    CANDIDATE_STATUS_EXCLUDED,
    CANDIDATE_STATUS_UNCERTAIN,
    PolicyCandidate,
)


PolicyChunkSearcher = Callable[..., Awaitable[list[EvidenceChunk]]]


class RecommendationService:
    def __init__(
        self,
        chunk_searcher: PolicyChunkSearcher = search_policy_chunks,
        result_limit: int = 5,
    ) -> None:
        self.chunk_searcher = chunk_searcher
        self.result_limit = result_limit

    async def build_result(
        self,
        merged_condition_json: dict[str, Any],
        candidates: list[PolicyCandidate],
        selected_candidates: list[PolicyCandidate] | None = None,
    ) -> dict[str, Any]:
        if selected_candidates is None:
            selected_candidates = [
                candidate
                for candidate in candidates
                if candidate.candidate_status != CANDIDATE_STATUS_EXCLUDED
            ][: self.result_limit]
        evidences, evidence_error = await self._search_evidences(
            condition=merged_condition_json,
            policy_ids=[
                candidate.policy.policy_id for candidate in selected_candidates
            ],
        )
        evidence_by_policy = self._group_evidences(evidences)

        results = [
            self._to_result_item(
                candidate,
                evidence_by_policy.get(str(candidate.policy.policy_id), []),
            )
            for candidate in selected_candidates
        ]
        results.sort(key=lambda item: item["match_score"], reverse=True)
        return {
            "results": results,
            "recommendations": results,
            "summary": {
                "candidate_count": len(selected_candidates),
                "result_count": len(results),
                "evidence_count": len(evidences),
                "evidence_error": evidence_error,
                "stored_candidate_count": len(candidates),
                "excluded_candidate_count": self._candidate_count(
                    candidates,
                    CANDIDATE_STATUS_EXCLUDED,
                ),
                "uncertain_candidate_count": self._candidate_count(
                    candidates,
                    CANDIDATE_STATUS_UNCERTAIN,
                ),
            },
        }

    async def _search_evidences(
        self,
        condition: dict[str, Any],
        policy_ids: list[int],
    ) -> tuple[list[EvidenceChunk], str | None]:
        if not policy_ids:
            return [], None
        query = self._evidence_query(condition)
        try:
            return (
                await self.chunk_searcher(
                    query=query,
                    policy_ids=policy_ids,
                    top_k=max(len(policy_ids) * 2, 5),
                    evidence_role="recommendation_reason",
                ),
                None,
            )
        except Exception as exc:
            return [], str(exc)

    def _to_result_item(
        self,
        candidate: PolicyCandidate,
        evidences: list[EvidenceChunk],
    ) -> dict[str, Any]:
        evidence_items = [evidence.model_dump(mode="json") for evidence in evidences]
        match_score = min(
            round(candidate.match_score + (0.05 if evidence_items else 0), 4),
            1.0,
        )
        reason = self._reason(candidate.matched_rules, bool(evidence_items))
        return {
            "policy_id": str(candidate.policy.policy_id),
            "policy_code": candidate.policy.policy_code,
            "slug": candidate.policy.policy_code,
            "policy_name": candidate.policy.policy_name,
            "summary": self._summary(candidate.detail, candidate.policy),
            "benefit_summary": self._short_text(
                candidate.detail.benefit_description if candidate.detail else None
            ),
            "match_score": match_score,
            "retrieval_score": candidate.retrieval_score,
            "candidate_status": candidate.candidate_status,
            "filter_match_json": candidate.filter_match_json,
            "reason": reason,
            "reason_summary": reason,
            "evidence": evidence_items,
            "evidences": evidence_items,
        }

    def _evidence_query(self, condition: dict[str, Any]) -> str:
        needs = " ".join(self._string_list(condition.get("needs")))
        stage = self._first(condition, "stage", "life_stage", "target_stage")
        stage_label = LIFE_STAGE_TO_DB.get(str(stage)) if stage else ""
        return " ".join(
            item
            for item in [
                needs,
                stage_label,
                "지원대상 선정기준 신청조건",
            ]
            if item
        )

    def _reason(self, matched_rules: list[str], has_evidence: bool) -> str:
        rules = self._deduplicate(matched_rules)
        if has_evidence:
            rules.append("정책 문서 근거가 확인됨")
        if not rules:
            return "현재 입력 조건과 비교 가능한 정책입니다."
        return ", ".join(rules[:4])

    def _summary(
        self,
        detail: Any,
        policy: Any,
    ) -> str:
        if detail is not None:
            summary = self._short_text(
                detail.easy_summary
                or detail.benefit_description
                or detail.target_description
            )
            if summary:
                return summary
        return policy.benefit_type or policy.policy_name

    def _short_text(self, value: str | None, limit: int = 180) -> str:
        if not value:
            return ""
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}..."

    def _group_evidences(
        self,
        evidences: list[EvidenceChunk],
    ) -> dict[str, list[EvidenceChunk]]:
        grouped: dict[str, list[EvidenceChunk]] = defaultdict(list)
        for evidence in evidences:
            grouped[str(evidence.policy_id)].append(evidence)
        return grouped

    def _first(self, condition: dict[str, Any], *keys: str) -> Any:
        for key in keys:
            value = condition.get(key)
            if value not in (None, "", []):
                return value
        return None

    def _string_list(self, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if item not in (None, "")]
        return [str(value)]

    def _deduplicate(self, values: list[str]) -> list[str]:
        deduplicated: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduplicated.append(normalized)
        return deduplicated

    def _candidate_count(
        self,
        candidates: list[PolicyCandidate],
        candidate_status: str,
    ) -> int:
        return sum(
            1
            for candidate in candidates
            if candidate.candidate_status == candidate_status
        )

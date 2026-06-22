from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
from app.common.policy_types import LIFE_STAGE_TO_DB
from app.db.models.policy import Policy
from app.db.models.policy_detail import PolicyDetail
from app.schemas.ai_contract import EvidenceChunk


PolicyChunkSearcher = Callable[..., Awaitable[list[EvidenceChunk]]]
SPECIAL_RECOMMENDATION_TERMS = {
    "single": "한부모",
    "multi": "다문화",
    "disabled": "장애",
    "many": "다자녀",
    "dual": "맞벌이",
    "veteran": "보훈",
}


@dataclass
class PolicyCandidate:
    policy: Policy
    detail: PolicyDetail | None
    match_score: float
    matched_rules: list[str]


class RecommendationService:
    def __init__(
        self,
        chunk_searcher: PolicyChunkSearcher = search_policy_chunks,
        result_limit: int = 5,
    ) -> None:
        self.chunk_searcher = chunk_searcher
        self.result_limit = result_limit

    async def recommend(
        self,
        db: AsyncSession,
        merged_condition_json: dict[str, Any],
    ) -> dict[str, Any]:
        candidates = await self._find_candidates(
            db=db,
            condition=merged_condition_json,
            limit=max(self.result_limit * 4, 20),
        )
        selected_candidates = candidates[: self.result_limit]
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
                "candidate_count": len(candidates),
                "result_count": len(results),
                "evidence_count": len(evidences),
                "evidence_error": evidence_error,
            },
        }

    async def _find_candidates(
        self,
        db: AsyncSession,
        condition: dict[str, Any],
        limit: int,
    ) -> list[PolicyCandidate]:
        query_terms = self._query_terms(condition)
        text_blob = self._policy_text_blob()
        statement = (
            select(Policy, PolicyDetail)
            .outerjoin(PolicyDetail, PolicyDetail.policy_id == Policy.policy_id)
            .where(Policy.is_active.is_(True))
            .order_by(Policy.policy_id)
            .limit(limit)
        )
        if query_terms:
            statement = statement.where(
                or_(*[text_blob.ilike(f"%{term}%") for term in query_terms])
            )

        rows = (await db.execute(statement)).all()
        candidates = [
            self._score_policy(policy=policy, detail=detail, condition=condition)
            for policy, detail in rows
        ]
        candidates = [
            candidate
            for candidate in candidates
            if candidate.matched_rules or not query_terms
        ]
        candidates.sort(key=lambda candidate: candidate.match_score, reverse=True)
        return candidates

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

    def _score_policy(
        self,
        policy: Policy,
        detail: PolicyDetail | None,
        condition: dict[str, Any],
    ) -> PolicyCandidate:
        text = self._policy_text(policy, detail)
        score = 0.0
        matched_rules: list[str] = []

        stage = self._first(condition, "stage", "life_stage", "target_stage")
        stage_label = LIFE_STAGE_TO_DB.get(str(stage)) if stage else None
        if stage_label and stage_label in text:
            score += 0.25
            matched_rules.append(f"생애주기 조건({stage_label})과 관련")

        child_age = self._first(condition, "childAge", "child_age", "child_age_range")
        if child_age and self._matches_child_age(str(child_age), text):
            score += 0.15
            matched_rules.append("자녀 연령 조건과 관련")

        region = self._first(condition, "region", "region_code")
        if policy.region_scope == "NATIONAL":
            score += 0.15
            matched_rules.append("전국 정책")
        elif region and policy.region_code == region:
            score += 0.2
            matched_rules.append("거주 지역 조건과 일치")

        income = self._first(condition, "income", "income_level", "income_bracket")
        if income and ("소득" in text or "중위소득" in text):
            score += 0.1
            matched_rules.append("소득 조건 확인 대상")

        special_score, special_matches = self._special_score(condition, text)
        score += special_score
        matched_rules.extend(special_matches)

        needs_score, needs_matches = self._needs_score(condition, text)
        score += needs_score
        matched_rules.extend(needs_matches)

        return PolicyCandidate(
            policy=policy,
            detail=detail,
            match_score=min(round(score, 4), 1.0),
            matched_rules=matched_rules,
        )

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
            "reason": reason,
            "reason_summary": reason,
            "evidence": evidence_items,
            "evidences": evidence_items,
        }

    def _policy_text_blob(self):
        return func.concat_ws(
            " ",
            Policy.policy_name,
            Policy.main_category,
            Policy.sub_category,
            Policy.provider_name,
            Policy.region_scope,
            Policy.region_code,
            Policy.benefit_type,
            PolicyDetail.easy_summary,
            PolicyDetail.target_description,
            PolicyDetail.benefit_description,
            PolicyDetail.caution,
        )

    def _policy_text(self, policy: Policy, detail: PolicyDetail | None) -> str:
        values = [
            policy.policy_name,
            policy.main_category,
            policy.sub_category,
            policy.provider_name,
            policy.region_scope,
            policy.region_code,
            policy.benefit_type,
        ]
        if detail is not None:
            values.extend(
                [
                    detail.easy_summary,
                    detail.target_description,
                    detail.benefit_description,
                    detail.application_method,
                    detail.caution,
                ]
            )
        return " ".join(str(value) for value in values if value)

    def _query_terms(self, condition: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        stage = self._first(condition, "stage", "life_stage", "target_stage")
        stage_label = LIFE_STAGE_TO_DB.get(str(stage)) if stage else None
        if stage_label:
            terms.append(stage_label)
        child_age = self._first(condition, "childAge", "child_age", "child_age_range")
        if child_age:
            terms.extend(self._child_age_terms(str(child_age)))
        for item in self._string_list(condition.get("special")):
            label = SPECIAL_RECOMMENDATION_TERMS.get(item)
            if label:
                terms.append(label)
        for need in self._string_list(condition.get("needs")):
            terms.extend(self._need_terms(need))
        return self._deduplicate(terms)

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

    def _special_score(
        self,
        condition: dict[str, Any],
        text: str,
    ) -> tuple[float, list[str]]:
        score = 0.0
        matches: list[str] = []
        for item in self._string_list(condition.get("special")):
            label = SPECIAL_RECOMMENDATION_TERMS.get(item)
            if label and label in text:
                score += 0.08
                matches.append(f"가구 특성({label})과 관련")
        return min(score, 0.16), matches[:2]

    def _needs_score(
        self,
        condition: dict[str, Any],
        text: str,
    ) -> tuple[float, list[str]]:
        score = 0.0
        matches: list[str] = []
        for need in self._string_list(condition.get("needs")):
            if any(term in text for term in self._need_terms(need)):
                score += 0.08
                matches.append(f"요청 관심사({need})와 관련")
        return min(score, 0.16), matches[:2]

    def _reason(self, matched_rules: list[str], has_evidence: bool) -> str:
        rules = self._deduplicate(matched_rules)
        if has_evidence:
            rules.append("정책 문서 근거가 확인됨")
        if not rules:
            return "현재 입력 조건과 비교 가능한 정책입니다."
        return ", ".join(rules[:4])

    def _summary(
        self,
        detail: PolicyDetail | None,
        policy: Policy,
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

    def _matches_child_age(self, child_age: str, text: str) -> bool:
        return any(term in text for term in self._child_age_terms(child_age))

    def _child_age_terms(self, child_age: str) -> list[str]:
        if child_age == "preborn":
            return ["임신", "출산", "태아"]
        if child_age in {"0", "1", "2-5"}:
            return ["영유아", "아동", "보육", "양육"]
        if child_age == "6-12":
            return ["아동", "초등"]
        if child_age == "13+":
            return ["청소년", "중고등"]
        return [child_age]

    def _need_terms(self, need: str) -> list[str]:
        return [
            token
            for token in need.replace("/", " ").split()
            if len(token.strip()) >= 2
        ][:4]

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

import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Any

from app.common.ai_status import AssessmentStatus
from app.core.config import settings
from app.schemas.recommendation_rerank_schema import (
    LlmRecommendationItem,
    LlmRecommendationRerankResult,
)
from app.services.recommendation_assessment_service import (
    RecommendationPolicyAssessment,
)
from app.services.recommendation_candidate_service import (
    CANDIDATE_STATUS_EXCLUDED,
    PolicyCandidate,
)


@dataclass
class RecommendationRerankOutput:
    result_json: dict[str, Any]
    rerank_scores: dict[int, float]
    fallback_used: bool
    error: str | None = None


class RecommendationRerankService:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 30,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    def select_candidate_pool(
        self,
        candidates: list[PolicyCandidate],
        assessments: list[RecommendationPolicyAssessment],
        result_limit: int,
    ) -> list[PolicyCandidate]:
        assessment_by_policy = self._assessment_by_policy(assessments)
        max_candidates = min(result_limit * 3, 15)
        eligible = [
            candidate
            for candidate in candidates
            if self._is_eligible_candidate(candidate, assessment_by_policy)
        ]
        eligible.sort(
            key=lambda candidate: self._candidate_sort_key(
                candidate,
                assessment_by_policy.get(str(candidate.policy.policy_id)),
            )
        )
        return eligible[:max_candidates]

    async def rerank(
        self,
        merged_condition_json: dict[str, Any],
        candidates: list[PolicyCandidate],
        assessments: list[RecommendationPolicyAssessment],
        base_result_json: dict[str, Any],
        result_limit: int,
    ) -> RecommendationRerankOutput:
        _ = candidates, assessments
        candidate_items = self._candidate_items(base_result_json)
        if not candidate_items:
            return self._fallback(
                base_result_json,
                result_limit,
                "LLM rerank candidate pool is empty",
            )

        try:
            llm_result = await self._call_llm(
                merged_condition_json=merged_condition_json,
                candidate_items=candidate_items,
                result_limit=result_limit,
            )
            sanitized = self._sanitize_llm_result(
                llm_result,
                candidate_items,
                result_limit,
            )
            if not sanitized.recommendations:
                return self._fallback(
                    base_result_json,
                    result_limit,
                    "LLM rerank returned no valid recommendations",
                )
            return self._apply_llm_result(
                base_result_json=base_result_json,
                llm_result=sanitized,
                result_limit=result_limit,
            )
        except Exception as exc:
            return self._fallback(base_result_json, result_limit, str(exc))

    async def _call_llm(
        self,
        merged_condition_json: dict[str, Any],
        candidate_items: list[dict[str, Any]],
        result_limit: int,
    ) -> LlmRecommendationRerankResult:
        from langchain_openai import ChatOpenAI

        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
        }
        if settings.openai_api_key:
            llm_kwargs["api_key"] = settings.openai_api_key

        structured_llm = ChatOpenAI(**llm_kwargs).with_structured_output(
            LlmRecommendationRerankResult
        )
        messages = [
            (
                "system",
                """
                너는 한국 복지정책 추천 서비스의 추천 설명 생성기다.

                너의 역할은 이미 DB 검색, Rule Filter, Policy Assessment를 통과한
                후보 정책 목록 안에서만 최종 추천 순서를 정하고, 사용자에게 보여줄
                추천 이유를 한글로 정리하는 것이다.

                반드시 지켜야 할 규칙:
                1. 입력 후보 목록에 없는 policy_id는 절대 추천하지 않는다.
                2. evidence에 없는 내용을 정책 근거처럼 말하지 않는다.
                3. 지원 가능 여부를 확정적으로 단정하지 않는다.
                4. 추가 확인이 필요한 상태는 그 점을 설명한다.
                5. NOT_MATCH 또는 EXCLUDED 후보는 추천하지 않는다.
                6. reason_summary는 짧고 자연스러운 한글 문장으로 작성한다.
                7. used_evidence_chunk_ids는 입력 evidence에 존재하는 chunk_id만 사용한다.
                8. 출력은 지정된 JSON schema만 따른다.
                9. 입력 후보가 충분하면 recommendations는 가능하면 result_limit개를 반환한다.
                   단, 추천할 수 없는 후보를 억지로 포함하지는 않는다.
                """,
            ),
            (
                "user",
                json.dumps(
                    {
                        "result_limit": result_limit,
                        "merged_condition_json": merged_condition_json,
                        "candidate_policies": candidate_items,
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        result = await asyncio.wait_for(
            structured_llm.ainvoke(messages),
            timeout=self.timeout_seconds,
        )
        if isinstance(result, LlmRecommendationRerankResult):
            return result
        if isinstance(result, dict):
            return LlmRecommendationRerankResult.model_validate(result)
        raise ValueError("LLM rerank response did not match schema")

    def _sanitize_llm_result(
        self,
        llm_result: LlmRecommendationRerankResult,
        candidate_items: list[dict[str, Any]],
        result_limit: int,
    ) -> LlmRecommendationRerankResult:
        candidate_by_policy = {
            str(item["policy_id"]): item
            for item in candidate_items
            if item.get("policy_id") is not None
        }
        sanitized_items: list[LlmRecommendationItem] = []
        seen_policy_ids: set[str] = set()

        for item in llm_result.recommendations:
            policy_id = str(item.policy_id)
            candidate = candidate_by_policy.get(policy_id)
            if candidate is None or policy_id in seen_policy_ids:
                continue

            allowed_chunk_ids = {
                str(evidence.get("chunk_id"))
                for evidence in candidate.get("evidence", [])
                if evidence.get("chunk_id") is not None
            }
            sanitized_items.append(
                item.model_copy(
                    update={
                        "policy_id": policy_id,
                        "used_evidence_chunk_ids": [
                            str(chunk_id)
                            for chunk_id in item.used_evidence_chunk_ids
                            if str(chunk_id) in allowed_chunk_ids
                        ],
                    }
                )
            )
            seen_policy_ids.add(policy_id)
            if len(sanitized_items) >= result_limit:
                break

        return LlmRecommendationRerankResult(
            recommendations=sanitized_items,
            summary_message=llm_result.summary_message,
        )

    def _apply_llm_result(
        self,
        base_result_json: dict[str, Any],
        llm_result: LlmRecommendationRerankResult,
        result_limit: int,
    ) -> RecommendationRerankOutput:
        result_json = copy.deepcopy(base_result_json)
        base_results = self._base_results(result_json)
        result_by_policy = {
            str(item.get("policy_id")): item
            for item in base_results
            if item.get("policy_id") is not None
        }
        final_results: list[dict[str, Any]] = []
        rerank_scores: dict[int, float] = {}
        selected_policy_ids: set[str] = set()

        for recommendation in llm_result.recommendations[:result_limit]:
            result_item = copy.deepcopy(result_by_policy.get(recommendation.policy_id))
            if result_item is None:
                continue
            result_item.update(
                {
                    "rerank_score": recommendation.rerank_score,
                    "reason_summary": recommendation.reason_summary,
                    "reason": recommendation.reason_summary,
                    "recommendation_reason": recommendation.recommendation_reason,
                    "manual_check_summary": recommendation.manual_check_summary,
                    "used_evidence_chunk_ids": (
                        recommendation.used_evidence_chunk_ids
                    ),
                }
            )
            final_results.append(result_item)
            selected_policy_ids.add(recommendation.policy_id)
            try:
                rerank_scores[int(recommendation.policy_id)] = (
                    recommendation.rerank_score
                )
            except ValueError:
                continue

        if not final_results:
            return self._fallback(
                base_result_json,
                result_limit,
                "LLM rerank result became empty after applying recommendations",
            )

        llm_selected_count = len(final_results)
        for base_item in base_results:
            if len(final_results) >= result_limit:
                break
            policy_id = str(base_item.get("policy_id"))
            if not policy_id or policy_id in selected_policy_ids:
                continue
            backfilled_item = copy.deepcopy(base_item)
            backfilled_item["llm_backfilled"] = True
            final_results.append(backfilled_item)
            selected_policy_ids.add(policy_id)
        llm_backfilled_count = len(final_results) - llm_selected_count

        summary = dict(result_json.get("summary") or {})
        summary.update(
            {
                "candidate_count": len(final_results),
                "result_count": len(final_results),
                "llm_rerank_used": True,
                "llm_fallback_used": False,
                "llm_error": None,
                "llm_summary_message": llm_result.summary_message,
                "llm_result_count": len(final_results),
                "llm_selected_count": llm_selected_count,
                "llm_backfilled_count": llm_backfilled_count,
                "llm_candidate_pool_count": len(base_results),
            }
        )
        result_json["results"] = final_results
        result_json["recommendations"] = final_results
        result_json["summary"] = summary
        return RecommendationRerankOutput(
            result_json=result_json,
            rerank_scores=rerank_scores,
            fallback_used=False,
            error=None,
        )

    def _fallback(
        self,
        base_result_json: dict[str, Any],
        result_limit: int,
        error: str,
    ) -> RecommendationRerankOutput:
        result_json = copy.deepcopy(base_result_json)
        fallback_results = self._base_results(result_json)[:result_limit]
        base_results = self._base_results(result_json)
        summary = dict(result_json.get("summary") or {})
        summary.update(
            {
                "candidate_count": len(fallback_results),
                "result_count": len(fallback_results),
                "llm_candidate_pool_count": len(base_results),
                "llm_result_count": 0,
                "llm_selected_count": 0,
                "llm_backfilled_count": 0,
                "llm_rerank_used": False,
                "llm_fallback_used": True,
                "llm_error": error,
            }
        )
        result_json["results"] = fallback_results
        result_json["recommendations"] = fallback_results
        result_json["summary"] = summary
        return RecommendationRerankOutput(
            result_json=result_json,
            rerank_scores={},
            fallback_used=True,
            error=error,
        )

    def _candidate_items(
        self,
        base_result_json: dict[str, Any],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for item in self._base_results(base_result_json):
            if item.get("candidate_status") == CANDIDATE_STATUS_EXCLUDED:
                continue
            if item.get("assessment_status") == AssessmentStatus.NOT_MATCH.value:
                continue
            items.append(
                {
                    "policy_id": str(item.get("policy_id")),
                    "policy_code": item.get("policy_code"),
                    "policy_name": item.get("policy_name"),
                    "summary": item.get("summary"),
                    "benefit_summary": item.get("benefit_summary"),
                    "retrieval_score": item.get("retrieval_score"),
                    "candidate_status": item.get("candidate_status"),
                    "user_status": item.get("user_status"),
                    "assessment_status": item.get("assessment_status"),
                    "confidence_score": item.get("confidence_score"),
                    "matched_conditions": item.get("matched_conditions") or [],
                    "missing_conditions": item.get("missing_conditions") or [],
                    "manual_check_points": item.get("manual_check_points") or [],
                    "reason_summary": item.get("reason_summary") or item.get("reason"),
                    "evidence": self._compact_evidences(item.get("evidence") or []),
                }
            )
        return items

    def _compact_evidences(
        self,
        evidences: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compacted: list[dict[str, Any]] = []
        for evidence in evidences[:3]:
            compacted.append(
                {
                    "chunk_id": str(evidence.get("chunk_id")),
                    "snippet": self._short_text(str(evidence.get("snippet") or "")),
                    "source_title": evidence.get("source_title"),
                    "source_url": evidence.get("source_url"),
                }
            )
        return compacted

    def _base_results(self, result_json: dict[str, Any]) -> list[dict[str, Any]]:
        value = result_json.get("results") or result_json.get("recommendations") or []
        return [item for item in value if isinstance(item, dict)]

    def _is_eligible_candidate(
        self,
        candidate: PolicyCandidate,
        assessment_by_policy: dict[str, RecommendationPolicyAssessment],
    ) -> bool:
        if candidate.candidate_status == CANDIDATE_STATUS_EXCLUDED:
            return False
        assessment = assessment_by_policy.get(str(candidate.policy.policy_id))
        if assessment is None:
            return True
        return assessment.assessment_status != AssessmentStatus.NOT_MATCH

    def _candidate_sort_key(
        self,
        candidate: PolicyCandidate,
        assessment: RecommendationPolicyAssessment | None,
    ) -> tuple[int, float]:
        priority_by_user_status = {
            "RECOMMENDABLE": 0,
            "NEEDS_CONFIRMATION": 1,
            "DIFFICULT_TO_RECOMMEND": 2,
        }
        user_status = assessment.user_status.value if assessment else ""
        return (
            priority_by_user_status.get(user_status, 1),
            -candidate.retrieval_score,
        )

    def _assessment_by_policy(
        self,
        assessments: list[RecommendationPolicyAssessment],
    ) -> dict[str, RecommendationPolicyAssessment]:
        return {
            str(assessment.policy_id): assessment
            for assessment in assessments
        }

    def _short_text(self, value: str, limit: int = 800) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[:limit].rstrip()}..."

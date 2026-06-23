from app.ai.states.recommendation_state import RecommendationGraphState
from app.repositories.policy_assessment_repository import PolicyAssessmentRepository
from app.services.recommendation_assessment_service import RecommendationAssessmentService
from app.services.recommendation_candidate_service import RecommendationCandidateService
from app.services.recommendation_rerank_service import RecommendationRerankService
from app.services.recommendation_service import RecommendationService


class RecommendationGraphNodes:
    def __init__(
        self,
        candidate_service: RecommendationCandidateService | None = None,
        recommendation_service: RecommendationService | None = None,
        assessment_service: RecommendationAssessmentService | None = None,
        assessment_repository: PolicyAssessmentRepository | None = None,
        rerank_service: RecommendationRerankService | None = None,
    ) -> None:
        self.candidate_service = candidate_service or RecommendationCandidateService()
        self.recommendation_service = recommendation_service or RecommendationService()
        self.assessment_service = (
            assessment_service or RecommendationAssessmentService()
        )
        self.assessment_repository = (
            assessment_repository or PolicyAssessmentRepository()
        )
        self.rerank_service = rerank_service or RecommendationRerankService()

    async def candidate_search(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        rows, query_terms = await self.candidate_service.search_candidates(
            db=state["db"],
            condition=state["merged_condition_json"],
            limit=max(self.recommendation_service.result_limit * 4, 20),
        )
        return {
            **state,
            "candidate_rows": rows,
            "query_terms": query_terms,
        }

    async def rule_filter(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        candidates = await self.candidate_service.rule_filter_candidates(
            db=state["db"],
            rows=state.get("candidate_rows", []),
            condition=state["merged_condition_json"],
            query_terms=state.get("query_terms", []),
        )
        return {
            **state,
            "candidates": candidates,
        }

    async def candidate_save(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        await self.candidate_service.save_candidates(
            db=state["db"],
            request_id=state["request_id"],
            candidates=state.get("candidates", []),
        )
        return state

    async def policy_assessment(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        assessments = await self.assessment_service.assess_candidates(
            merged_condition_json=state["merged_condition_json"],
            candidates=state.get("candidates", []),
            input_issues=state.get("input_issues", []),
            profile_conflict_json=state.get("profile_conflict_json", []),
            result_limit=self.recommendation_service.result_limit,
        )
        return {
            **state,
            "assessments": assessments,
        }

    async def assessment_save(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        await self.assessment_repository.replace_recommendation_assessments(
            db=state["db"],
            request_id=state["request_id"],
            assessments=state.get("assessments", []),
        )
        return state

    async def build_result(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        rerank_candidates = self.rerank_service.select_candidate_pool(
            candidates=state.get("candidates", []),
            assessments=state.get("assessments", []),
            result_limit=self.recommendation_service.result_limit,
        )
        result_json = await self.recommendation_service.build_result(
            merged_condition_json=state["merged_condition_json"],
            candidates=state.get("candidates", []),
            selected_candidates=rerank_candidates,
            assessments=state.get("assessments", []),
        )
        return {
            **state,
            "base_result_json": result_json,
            "result_json": result_json,
        }

    async def llm_rerank(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        rerank_result = await self.rerank_service.rerank(
            merged_condition_json=state["merged_condition_json"],
            candidates=state.get("candidates", []),
            assessments=state.get("assessments", []),
            base_result_json=state.get("base_result_json")
            or state.get("result_json", {}),
            result_limit=self.recommendation_service.result_limit,
            raw_query=state.get("raw_query"),
            selected_conditions=state.get("selected_conditions") or {},
            input_issues=state.get("input_issues", []),
            profile_conflict_json=state.get("profile_conflict_json", []),
        )
        return {
            **state,
            "llm_rerank_result": rerank_result,
            "llm_fallback_used": rerank_result.fallback_used,
            "llm_error": rerank_result.error,
            "result_json": rerank_result.result_json,
        }

    async def rerank_save(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        rerank_result = state.get("llm_rerank_result")
        if rerank_result and rerank_result.rerank_scores:
            await self.candidate_service.save_rerank_scores(
                db=state["db"],
                request_id=state["request_id"],
                rerank_scores=rerank_result.rerank_scores,
            )
        return state

    async def finalize_result(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        result_json = state.get("result_json") or state.get("base_result_json") or {
            "results": [],
            "recommendations": [],
        }
        return {
            **state,
            "result_json": result_json,
        }

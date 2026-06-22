from app.ai.states.recommendation_state import RecommendationGraphState
from app.services.recommendation_candidate_service import RecommendationCandidateService
from app.services.recommendation_service import RecommendationService


class RecommendationGraphNodes:
    def __init__(
        self,
        candidate_service: RecommendationCandidateService | None = None,
        recommendation_service: RecommendationService | None = None,
    ) -> None:
        self.candidate_service = candidate_service or RecommendationCandidateService()
        self.recommendation_service = recommendation_service or RecommendationService()

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

    async def build_result(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        result_json = await self.recommendation_service.build_result(
            merged_condition_json=state["merged_condition_json"],
            candidates=state.get("candidates", []),
        )
        return {
            **state,
            "result_json": result_json,
        }

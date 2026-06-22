from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.recommendation import RecommendationGraphNodes
from app.ai.states import RecommendationGraphState
from app.repositories.policy_assessment_repository import PolicyAssessmentRepository
from app.services.recommendation_assessment_service import RecommendationAssessmentService
from app.services.recommendation_candidate_service import RecommendationCandidateService
from app.services.recommendation_service import RecommendationService


class RecommendationGraphRunner:
    def __init__(
        self,
        candidate_service: RecommendationCandidateService | None = None,
        recommendation_service: RecommendationService | None = None,
        assessment_service: RecommendationAssessmentService | None = None,
        assessment_repository: PolicyAssessmentRepository | None = None,
    ) -> None:
        self.nodes = RecommendationGraphNodes(
            candidate_service=candidate_service,
            recommendation_service=recommendation_service,
            assessment_service=assessment_service,
            assessment_repository=assessment_repository,
        )
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(RecommendationGraphState)
        workflow.add_node("candidate_search", self.nodes.candidate_search)
        workflow.add_node("rule_filter", self.nodes.rule_filter)
        workflow.add_node("candidate_save", self.nodes.candidate_save)
        workflow.add_node("policy_assessment", self.nodes.policy_assessment)
        workflow.add_node("assessment_save", self.nodes.assessment_save)
        workflow.add_node("build_result", self.nodes.build_result)
        workflow.add_edge(START, "candidate_search")
        workflow.add_edge("candidate_search", "rule_filter")
        workflow.add_edge("rule_filter", "candidate_save")
        workflow.add_edge("candidate_save", "policy_assessment")
        workflow.add_edge("policy_assessment", "assessment_save")
        workflow.add_edge("assessment_save", "build_result")
        workflow.add_edge("build_result", END)
        return workflow.compile()

    async def run(
        self,
        db: AsyncSession,
        request_id: int,
        merged_condition_json: dict[str, Any],
        input_issues: list[dict[str, Any]] | None = None,
        profile_conflict_json: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        final_state = await self.graph.ainvoke(
            {
                "db": db,
                "request_id": request_id,
                "merged_condition_json": merged_condition_json,
                "input_issues": input_issues or [],
                "profile_conflict_json": profile_conflict_json or [],
            }
        )
        return final_state.get(
            "result_json",
            {"results": [], "recommendations": []},
        )

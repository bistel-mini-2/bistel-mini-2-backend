from typing import Any, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.recommendation_service import RecommendationService


class RecommendationGraphState(TypedDict):
    db: AsyncSession
    merged_condition_json: dict[str, Any]
    result_json: NotRequired[dict[str, Any]]


class RecommendationGraphRunner:
    def __init__(
        self,
        recommendation_service: RecommendationService | None = None,
    ) -> None:
        self.recommendation_service = recommendation_service or RecommendationService()
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(RecommendationGraphState)
        workflow.add_node("recommend", self._recommend_node)
        workflow.add_edge(START, "recommend")
        workflow.add_edge("recommend", END)
        return workflow.compile()

    async def _recommend_node(
        self,
        state: RecommendationGraphState,
    ) -> RecommendationGraphState:
        result_json = await self.recommendation_service.recommend(
            db=state["db"],
            merged_condition_json=state["merged_condition_json"],
        )
        return {
            **state,
            "result_json": result_json,
        }

    async def run(
        self,
        db: AsyncSession,
        merged_condition_json: dict[str, Any],
    ) -> dict[str, Any]:
        final_state = await self.graph.ainvoke(
            {
                "db": db,
                "merged_condition_json": merged_condition_json,
            }
        )
        return final_state.get(
            "result_json",
            {"results": [], "recommendations": []},
        )

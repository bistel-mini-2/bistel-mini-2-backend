from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.comparison import ComparisonGraphNodes
from app.ai.states.comparison_state import ComparisonGraphState
from app.services.compare_service import CompareService


class ComparisonGraphRunner:
    def __init__(self, compare_service: CompareService | None = None) -> None:
        self.nodes = ComparisonGraphNodes(compare_service=compare_service)
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(ComparisonGraphState)
        workflow.add_node("compare_policies", self.nodes.compare_policies)
        workflow.add_node("build_result", self.nodes.build_result)
        workflow.add_edge(START, "compare_policies")
        workflow.add_edge("compare_policies", "build_result")
        workflow.add_edge("build_result", END)
        return workflow.compile()

    async def run(
        self,
        db: AsyncSession,
        *,
        slug_a: str,
        slug_b: str,
        user_id: int | None = None,
        raw_query: str | None = None,
    ) -> dict[str, Any]:
        final_state = await self.graph.ainvoke(
            {
                "db": db,
                "user_id": user_id,
                "slug_a": slug_a,
                "slug_b": slug_b,
                "raw_query": raw_query,
            }
        )
        return final_state.get("result_json", {})


comparison_graph_runner = ComparisonGraphRunner()
comparison_graph = comparison_graph_runner.graph


__all__ = [
    "ComparisonGraphRunner",
    "comparison_graph",
    "comparison_graph_runner",
]

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.eligibility import EligibilityGraphNodes
from app.ai.states.eligibility_state import EligibilityGraphState

if TYPE_CHECKING:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


class EligibilityGraphRunner:
    def __init__(
        self,
        lifecycle_service: AiRequestLifecycleService | None = None,
    ) -> None:
        self.nodes = EligibilityGraphNodes(lifecycle_service=lifecycle_service)
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(EligibilityGraphState)
        workflow.add_node("create_request", self.nodes.create_request)
        workflow.add_node("mark_processing", self.nodes.mark_processing)
        workflow.add_node("assess_policy", self.nodes.assess_policy)
        workflow.add_node("build_result", self.nodes.build_result)
        workflow.add_edge(START, "create_request")
        workflow.add_edge("create_request", "mark_processing")
        workflow.add_edge("mark_processing", "assess_policy")
        workflow.add_edge("assess_policy", "build_result")
        workflow.add_edge("build_result", END)
        return workflow.compile()

    async def run(
        self,
        db: AsyncSession,
        user_id: int,
        policy_identifier: int | str,
        raw_query: str | None = None,
        selected_conditions: dict[str, Any] | None = None,
        source_type: str = "CHAT",
        source_ref_id: str | None = None,
        follow_up_resolved: bool = False,
    ) -> dict[str, Any]:
        final_state = await self.graph.ainvoke(
            {
                "db": db,
                "user_id": user_id,
                "policy_identifier": policy_identifier,
                "raw_query": raw_query,
                "selected_conditions": selected_conditions,
                "source_type": source_type,
                "source_ref_id": source_ref_id,
                "follow_up_resolved": follow_up_resolved,
            }
        )
        return final_state.get("result_json", {})


eligibility_graph_runner = EligibilityGraphRunner()
eligibility_graph = eligibility_graph_runner.graph


__all__ = [
    "EligibilityGraphRunner",
    "eligibility_graph",
    "eligibility_graph_runner",
]

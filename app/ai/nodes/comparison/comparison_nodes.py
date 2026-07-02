from __future__ import annotations

from typing import Any

from app.ai.states.comparison_state import ComparisonGraphState
from app.ai.utils.progress import progress_node
from app.services.compare_service import CompareService


class ComparisonGraphNodes:
    def __init__(self, compare_service: CompareService | None = None) -> None:
        self.compare_service = compare_service or CompareService()

    @progress_node("comparison", "compare_policies")
    async def compare_policies(
        self,
        state: ComparisonGraphState,
    ) -> ComparisonGraphState:
        result = await self.compare_service.compare_policies(
            state["db"],
            slug_a=state["slug_a"],
            slug_b=state["slug_b"],
            user_id=state.get("user_id"),
        )
        return {**state, "compare_result": result}

    @progress_node("comparison", "build_result")
    async def build_result(
        self,
        state: ComparisonGraphState,
    ) -> ComparisonGraphState:
        result = state["compare_result"]
        diff_items = [
            {
                "field": item.field,
                "a": item.a,
                "b": item.b,
            }
            for item in result.diff_table
        ]
        result_json: dict[str, Any] = {
            "policy_a": result.policy_a.model_dump(),
            "policy_b": result.policy_b.model_dump(),
            "diff_table": diff_items,
            "selection_guide": result.selection_guide,
            "related_policies": [
                policy.model_dump() for policy in result.related_policies
            ],
        }
        return {**state, "result_json": result_json}

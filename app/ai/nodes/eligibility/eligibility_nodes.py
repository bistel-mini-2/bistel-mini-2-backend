from __future__ import annotations

from typing import TYPE_CHECKING

from app.ai.states.eligibility_state import EligibilityGraphState

if TYPE_CHECKING:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def _lifecycle_service_class() -> type["AiRequestLifecycleService"]:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService

    return AiRequestLifecycleService


class EligibilityGraphNodes:
    def __init__(
        self,
        lifecycle_service: AiRequestLifecycleService | None = None,
    ) -> None:
        self.lifecycle_service = lifecycle_service

    def _lifecycle(self) -> AiRequestLifecycleService:
        return self.lifecycle_service or _lifecycle_service_class()()

    async def create_request(
        self,
        state: EligibilityGraphState,
    ) -> EligibilityGraphState:
        snapshot = await self._lifecycle().create_eligibility_request(
            db=state["db"],
            user_id=state["user_id"],
            policy_identifier=state["policy_identifier"],
            source_type=state["source_type"],
            source_ref_id=state.get("source_ref_id"),
            raw_query=state.get("raw_query"),
            selected_conditions=state.get("selected_conditions"),
        )
        return {**state, "request_id": int(snapshot.request_id)}

    async def mark_processing(
        self,
        state: EligibilityGraphState,
    ) -> EligibilityGraphState:
        await self._lifecycle().mark_processing(
            db=state["db"],
            request_type="eligibility",
            request_id=state["request_id"],
        )
        return state

    async def assess_policy(
        self,
        state: EligibilityGraphState,
    ) -> EligibilityGraphState:
        await self._lifecycle().process_condition_request(
            db=state["db"],
            request_type="eligibility",
            request_id=state["request_id"],
        )
        return state

    async def build_result(
        self,
        state: EligibilityGraphState,
    ) -> EligibilityGraphState:
        response = await self._lifecycle().get_eligibility_result(
            db=state["db"],
            request_id=state["request_id"],
            user_id=state["user_id"],
        )
        return {**state, "result_json": response.model_dump(mode="json")}

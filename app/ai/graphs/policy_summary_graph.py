import json
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.ai.agents.policy_summary_agent import PolicySummaryAgent
from app.ai.states.policy_summary_state import PolicySummaryGraphState
from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
from app.ai.utils.policy_summary_utils import build_policy_summary_key_points
from app.ai.utils.progress import progress_node
from app.schemas.ai_contract import EvidenceChunk


class PolicySummaryGraphRunner:
    def __init__(
        self,
        agent: PolicySummaryAgent | None = None,
    ) -> None:
        self.agent = agent or PolicySummaryAgent()
        workflow = StateGraph(PolicySummaryGraphState)
        workflow.add_node("summary_evidence_search", self.summary_evidence_search)
        workflow.add_node("policy_summary", self.policy_summary)
        workflow.add_edge(START, "summary_evidence_search")
        workflow.add_edge("summary_evidence_search", "policy_summary")
        workflow.add_edge("policy_summary", END)
        self.graph = workflow.compile()

    async def run(self, policy: dict[str, Any]) -> dict[str, Any]:
        final_state = await self.graph.ainvoke({"policy": policy})
        summary = final_state.get("summary") or ""
        return {
            "summary": summary,
            "easy_summary": summary,
            "key_points": build_policy_summary_key_points(policy),
            "evidence": list(final_state.get("evidence") or []),
            "evidence_chunks": list(final_state.get("evidence_chunks") or []),
        }

    @progress_node("policy_summary", "summary_evidence_search")
    async def summary_evidence_search(
        self,
        state: PolicySummaryGraphState,
    ) -> PolicySummaryGraphState:
        policy = state["policy"]
        query = _build_evidence_query(policy)
        chunks: list[EvidenceChunk] = []
        if query:
            try:
                chunks = await search_policy_chunks(
                    query=query[:500],
                    policy_ids=[policy["policy_id"]],
                    top_k=5,
                )
            except Exception:
                chunks = []
        return {**state, "evidence_chunks": chunks}

    @progress_node("policy_summary", "policy_summary")
    async def policy_summary(
        self,
        state: PolicySummaryGraphState,
    ) -> PolicySummaryGraphState:
        result = await self.agent.summarize(
            policy=state["policy"],
            evidence_chunks=list(state.get("evidence_chunks") or []),
        )
        return {
            **state,
            "summary": result.summary,
            "evidence": result.evidence,
        }

def _build_evidence_query(policy: dict[str, Any]) -> str:
    condition_json = _condition_json(policy)
    condition_profile_text = " ".join(
        str(value)
        for value in (
            policy.get("condition_profile_source_text"),
            policy.get("condition_profile_target_summary"),
            _json_text(condition_json),
        )
        if value
    )
    return " ".join(
        str(value)
        for value in (
            condition_profile_text,
            policy.get("name"),
            None if condition_profile_text else policy.get("target_description"),
            policy.get("benefit_description"),
            policy.get("application_method"),
        )
        if value
    )


def _condition_json(policy: dict[str, Any]) -> dict[str, Any]:
    value = policy.get("condition_profile_json")
    return value if isinstance(value, dict) else {}


def _json_text(value: Any) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)

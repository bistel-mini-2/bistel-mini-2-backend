import json
from typing import Any

from langgraph.graph import END, START, StateGraph

from app.ai.agents.policy_summary_agent import PolicySummaryAgent
from app.ai.states.policy_summary_state import PolicySummaryGraphState
from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
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
            "key_points": _build_key_points(policy),
            "evidence": list(final_state.get("evidence") or []),
            "evidence_chunks": list(final_state.get("evidence_chunks") or []),
        }

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


def _build_key_points(policy: dict[str, Any]) -> list[dict[str, str]]:
    candidates = _condition_profile_key_point_candidates(policy)
    key_points: list[dict[str, str]] = []
    for label, value in candidates:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        key_points.append({"label": label, "content": _short(text, 160)})
    return key_points[:3]


def _condition_profile_key_point_candidates(
    policy: dict[str, Any],
) -> tuple[tuple[str, Any], ...]:
    condition_json = _condition_json(policy)
    condition_source = policy.get("condition_profile_source_text")
    target_summary = policy.get("condition_profile_target_summary")
    condition_tree = condition_json.get("condition_tree")
    exclusions = condition_json.get("exclusions")
    unsupported = condition_json.get("unsupported_conditions")
    unknowns = condition_json.get("unknowns")

    target = (
        target_summary
        or _summarize_condition_tree(condition_tree)
        or condition_source
        or policy.get("target_description")
    )
    verification_notes = _summarize_condition_profile_notes(
        exclusions=exclusions,
        unsupported=unsupported,
        unknowns=unknowns,
    )
    return (
        ("target", target),
        (
            "benefit",
            policy.get("benefit_description") or policy.get("benefit_summary"),
        ),
        (
            "application",
            policy.get("application_method")
            or policy.get("application_period_text"),
        ),
        ("condition_check", verification_notes),
    )


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


def _summarize_condition_tree(value: Any) -> str:
    leaves = _condition_leaves(value)
    parts: list[str] = []
    for leaf in leaves[:4]:
        source_text = leaf.get("source_text")
        if source_text:
            parts.append(str(source_text))
            continue
        field = leaf.get("field")
        operator = leaf.get("operator")
        leaf_value = leaf.get("value")
        if field and operator:
            parts.append(f"{field} {operator} {leaf_value}")
    return " / ".join(part for part in parts if part)


def _condition_leaves(value: Any) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    if not isinstance(value, dict):
        return leaves
    children = value.get("conditions")
    if isinstance(children, list):
        for child in children:
            leaves.extend(_condition_leaves(child))
    elif value.get("field"):
        leaves.append(value)
    return leaves


def _summarize_condition_profile_notes(
    *,
    exclusions: Any,
    unsupported: Any,
    unknowns: Any,
) -> str:
    notes: list[str] = []
    for label, items in (
        ("exclusions", exclusions),
        ("manual review", unsupported),
        ("unknown", unknowns),
    ):
        if isinstance(items, list) and items:
            text = _first_source_text(items)
            notes.append(f"{label}: {text}" if text else label)
    return " / ".join(notes)


def _first_source_text(items: list[Any]) -> str:
    for item in items:
        if isinstance(item, dict):
            value = item.get("source_text") or item.get("reason")
            if value:
                return str(value)
    return ""


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

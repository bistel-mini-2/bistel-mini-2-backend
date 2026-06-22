from langgraph.graph import END, START, StateGraph

from app.ai.nodes.chat import ChatGraphNodes
from app.ai.states.chat_state import ChatGraphState, Intent
from app.services.policy_rag_service import PolicyRagService


HISTORY_LIMIT = 5


_BRANCH_NAMES: tuple[Intent, ...] = (
    "recommend",
    "eligibility",
    "compare",
    "apply",
    "policy_summary",
    "unclear",
)


class ChatSupervisorGraphRunner:
    def __init__(self, rag_service: PolicyRagService | None = None) -> None:
        self.nodes = ChatGraphNodes(rag_service=rag_service)
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(ChatGraphState)
        workflow.add_node("supervisor", self.nodes.supervisor)
        workflow.add_node("recommend", self.nodes.branch_recommend)
        workflow.add_node("eligibility", self.nodes.branch_eligibility)
        workflow.add_node("compare", self.nodes.branch_compare)
        workflow.add_node("apply", self.nodes.branch_apply)
        workflow.add_node("policy_summary", self.nodes.branch_policy_summary)
        workflow.add_node("unclear", self.nodes.branch_unclear)
        workflow.add_node("assistant_payload_build", self.nodes.assistant_payload_build)
        workflow.add_node("evidence_extract", self.nodes.evidence_extract)
        workflow.add_node("policy_link_extract", self.nodes.policy_link_extract)

        workflow.add_edge(START, "supervisor")
        workflow.add_conditional_edges(
            "supervisor",
            _route_by_intent,
            {name: name for name in _BRANCH_NAMES},
        )
        for branch in _BRANCH_NAMES:
            workflow.add_edge(branch, "assistant_payload_build")
        workflow.add_edge("assistant_payload_build", "evidence_extract")
        workflow.add_edge("evidence_extract", "policy_link_extract")
        workflow.add_edge("policy_link_extract", END)
        return workflow.compile()


def _route_by_intent(state: ChatGraphState) -> Intent:
    decision = state.get("supervisor_decision")
    if decision is None:
        return "unclear"
    return decision["intent"]


chat_supervisor_graph_runner = ChatSupervisorGraphRunner()
chat_supervisor_graph = chat_supervisor_graph_runner.graph


__all__ = [
    "ChatSupervisorGraphRunner",
    "HISTORY_LIMIT",
    "chat_supervisor_graph",
    "chat_supervisor_graph_runner",
]

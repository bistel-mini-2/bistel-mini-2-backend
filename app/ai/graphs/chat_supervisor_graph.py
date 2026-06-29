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
    "summary",
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
        workflow.add_node("recommend_retry_increment", self.nodes.recommend_retry_increment)
        workflow.add_node("recommend_fallback_build", self.nodes.recommend_fallback_build)
        workflow.add_node("eligibility", self.nodes.branch_eligibility)
        workflow.add_node("compare", self.nodes.branch_compare)
        workflow.add_node("apply", self.nodes.branch_apply)
        workflow.add_node("apply_retry_increment", self.nodes.apply_retry_increment)
        workflow.add_node("apply_fallback_build", self.nodes.apply_fallback_build)
        workflow.add_node("policy_summary", self.nodes.branch_policy_summary)
        workflow.add_node("summary", self.nodes.branch_summary)
        workflow.add_node("unclear", self.nodes.branch_unclear)
        workflow.add_node("collect_slots", self.nodes.collect_slots)
        workflow.add_node("confirm_profile", self.nodes.confirm_profile)
        workflow.add_node("assistant_payload_build", self.nodes.assistant_payload_build)
        workflow.add_node("evidence_extract", self.nodes.evidence_extract)
        workflow.add_node("policy_link_extract", self.nodes.policy_link_extract)

        workflow.add_edge(START, "supervisor")
        workflow.add_conditional_edges(
            "supervisor",
            _route_by_intent,
            {
                **{name: name for name in _BRANCH_NAMES},
                "collect_slots": "collect_slots",
                "confirm_profile": "confirm_profile",
            },
        )
        workflow.add_edge("collect_slots", "assistant_payload_build")
        workflow.add_edge("confirm_profile", "assistant_payload_build")
        workflow.add_conditional_edges(
            "recommend",
            _route_recommend_result,
            {
                "ok": "assistant_payload_build",
                "retryable_error": "recommend_retry_increment",
                "fallback_needed": "recommend_fallback_build",
            },
        )
        workflow.add_conditional_edges(
            "recommend_retry_increment",
            _route_recommend_retry,
            {
                "retry": "recommend",
                "fallback": "recommend_fallback_build",
            },
        )
        workflow.add_edge("recommend_fallback_build", "assistant_payload_build")
        for branch in ("eligibility", "compare", "summary", "policy_summary", "unclear"):
            workflow.add_edge(branch, "assistant_payload_build")
        workflow.add_conditional_edges(
            "apply",
            _route_apply_result,
            {
                "ok": "assistant_payload_build",
                "retryable_error": "apply_retry_increment",
                "fallback_needed": "apply_fallback_build",
            },
        )
        workflow.add_conditional_edges(
            "apply_retry_increment",
            _route_apply_retry,
            {
                "retry": "apply",
                "fallback": "apply_fallback_build",
            },
        )
        workflow.add_edge("apply_fallback_build", "assistant_payload_build")
        workflow.add_edge("assistant_payload_build", "evidence_extract")
        workflow.add_edge("evidence_extract", "policy_link_extract")
        workflow.add_edge("policy_link_extract", END)
        return workflow.compile()


def _route_by_intent(state: ChatGraphState) -> str:
    # 저장된 회원 프로필이 있으면 추천 전에 "이대로 진행?" 확인부터.
    if state.get("profile_confirm"):
        return "confirm_profile"
    # 필수 슬롯이 비어 있으면 branch로 가기 전에 되묻는다.
    if state.get("awaiting_slots"):
        return "collect_slots"
    decision = state.get("supervisor_decision")
    if decision is None:
        return "unclear"
    return decision["intent"]


def _route_recommend_result(state: ChatGraphState) -> str:
    return state.get("recommend_flow_status", "ok")


def _route_recommend_retry(state: ChatGraphState) -> str:
    retry_count = int(state.get("recommend_retry_count") or 0)
    max_retries = int(state.get("recommend_max_retries") or 1)
    return "retry" if retry_count <= max_retries else "fallback"


def _route_apply_result(state: ChatGraphState) -> str:
    return state.get("apply_flow_status", "ok")


def _route_apply_retry(state: ChatGraphState) -> str:
    retry_count = int(state.get("apply_retry_count") or 0)
    max_retries = int(state.get("apply_max_retries") or 1)
    return "retry" if retry_count <= max_retries else "fallback"


chat_supervisor_graph_runner = ChatSupervisorGraphRunner()
chat_supervisor_graph = chat_supervisor_graph_runner.graph


__all__ = [
    "ChatSupervisorGraphRunner",
    "HISTORY_LIMIT",
    "chat_supervisor_graph",
    "chat_supervisor_graph_runner",
]

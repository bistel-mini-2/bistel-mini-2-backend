from typing import Any, Literal, NotRequired, TypedDict


Intent = Literal[
    "recommend",
    "eligibility",
    "compare",
    "apply",
    "policy_summary",
    "unclear",
]


class HistoryMessage(TypedDict):
    role: str
    content: str | None


class SupervisorDecision(TypedDict):
    intent: Intent
    raw: str


class ChatGraphState(TypedDict):
    user_id: int
    user_content: str
    history: list[HistoryMessage]
    supervisor_decision: NotRequired[SupervisorDecision]
    branch_content: NotRequired[str]
    branch_policies: NotRequired[list[dict[str, Any]]]
    branch_evidences: NotRequired[list[dict[str, Any]]]
    branch_apply_card: NotRequired[dict[str, Any] | None]
    assistant_payload: NotRequired[dict[str, Any]]
    evidences_to_save: NotRequired[list[dict[str, Any]]]
    policy_links_to_save: NotRequired[list[dict[str, Any]]]

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


class SlotPolicy(TypedDict):
    policy_id: int
    slug: str
    policy_name: str
    last_action: str


class ChatSlot(TypedDict, total=False):
    recent_policies: list[SlotPolicy]
    updated_at: str


class SupervisorDecision(TypedDict):
    intent: Intent
    raw: str
    resolved_policy_slug: NotRequired[str | None]


class ChatGraphState(TypedDict):
    user_id: int
    user_content: str
    history: list[HistoryMessage]
    slot: NotRequired[ChatSlot]
    supervisor_decision: NotRequired[SupervisorDecision]
    branch_content: NotRequired[str]
    branch_policies: NotRequired[list[dict[str, Any]]]
    branch_evidences: NotRequired[list[dict[str, Any]]]
    branch_apply_card: NotRequired[dict[str, Any] | None]
    assistant_payload: NotRequired[dict[str, Any]]
    evidences_to_save: NotRequired[list[dict[str, Any]]]
    policy_links_to_save: NotRequired[list[dict[str, Any]]]

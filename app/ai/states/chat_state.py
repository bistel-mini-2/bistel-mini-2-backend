from typing import Any, Literal, NotRequired, TypedDict


Intent = Literal[
    "recommend",
    "eligibility",
    "compare",
    "apply",
    "summary",
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
    eligibility_request_id: NotRequired[int | None]
    follow_up_questions: NotRequired[list[dict] | None]
    eligibility_status: NotRequired[str | None]


class ProfileSlot(TypedDict, total=False):
    """세션 전역 사용자 조건. 모든 intent가 공유한다."""

    stage: str          # 생애단계: 임신/출산/영유아/초등
    child_age: str      # 자녀 나이
    income: str         # 소득 구간
    region: str         # 거주 지역
    special: list[str]  # 특이사항: 한부모/다자녀/맞벌이 등
    skipped: list[str]  # 사용자가 "건너뛰기"한 슬롯 (다시 묻지 않음)


class PendingState(TypedDict, total=False):
    """채우는 중인 intent와 아직 못 받은 슬롯. 다음 턴에 이어받기 위함."""

    intent: Intent
    awaiting: list[str]
    asked: list[str]
    kind: str  # "slot"(조건 칩 폼) | "confirm"(회원 프로필 확인)


class ChatSlot(TypedDict, total=False):
    recent_policies: list[SlotPolicy]
    profile: ProfileSlot
    pending: PendingState | None
    updated_at: str


class RecentAssistantPolicy(TypedDict):
    policy_id: int
    slug: str
    policy_name: str
    action_type: str


class SupervisorDecision(TypedDict):
    intent: Intent
    raw: str
    resolved_policy_slug: NotRequired[str | None]


class ChatGraphState(TypedDict):
    user_id: int
    user_content: str
    history: list[HistoryMessage]
    slot: NotRequired[ChatSlot]
    recent_assistant_policy: NotRequired[RecentAssistantPolicy | None]
    supervisor_decision: NotRequired[SupervisorDecision]
    # 슬롯 필링 / 이어받기
    profile: NotRequired[ProfileSlot]
    pending: NotRequired[PendingState | None]
    pending_intent: NotRequired[Intent | None]
    awaiting_slots: NotRequired[list[str]]
    slot_request: NotRequired[dict[str, Any] | None]
    profile_confirm: NotRequired[dict[str, Any] | None]
    branch_content: NotRequired[str]
    branch_user_status: NotRequired[str | None]
    branch_easy_summary: NotRequired[str | None]
    branch_key_points: NotRequired[list[dict[str, Any]]]
    branch_policies: NotRequired[list[dict[str, Any]]]
    branch_evidences: NotRequired[list[dict[str, Any]]]
    branch_apply_card: NotRequired[dict[str, Any] | None]
    recommend_flow_status: NotRequired[str]
    recommend_error_message: NotRequired[str | None]
    recommend_retry_count: NotRequired[int]
    recommend_max_retries: NotRequired[int]
    apply_flow_status: NotRequired[str]
    apply_error_message: NotRequired[str | None]
    apply_retry_count: NotRequired[int]
    apply_max_retries: NotRequired[int]
    assistant_payload: NotRequired[dict[str, Any]]
    evidences_to_save: NotRequired[list[dict[str, Any]]]
    policy_links_to_save: NotRequired[list[dict[str, Any]]]
    eligibility_slot_update: NotRequired[dict[str, Any] | None]

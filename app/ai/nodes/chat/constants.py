from __future__ import annotations

from app.ai.states.chat_state import Intent


INTENT_TO_API_ACTION: dict[Intent, str | None] = {
    "recommend": "recommend",
    "eligibility": "eligibility",
    "compare": "compare",
    "apply": "apply",
    "summary": None,
    "policy_summary": None,
    "unclear": None,
}

INTENT_TO_ACTION_TYPE: dict[Intent, str | None] = {
    "recommend": "RECOMMENDED",
    "compare": "COMPARED",
    "eligibility": "ELIGIBILITY_TARGET",
    "apply": "APPLY_TARGET",
    "summary": None,
    "policy_summary": None,
    "unclear": None,
}

LLM_MODEL = "gpt-5.4-mini"
RAG_TOP_K = 5
POLICIES_MAX = 3
EVIDENCES_MAX = 5
SNIPPET_LIMIT = 300

RECOMMEND_SOURCE_TYPE = "CHAT"
RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS = 60
RECOMMEND_LOCK_TIMEOUT = "5s"
RECOMMEND_STATEMENT_TIMEOUT = "60s"
RECOMMEND_FALLBACK_FOLLOW_UP = (
    "맞춤 추천을 위해 정보가 더 필요해요. 맞춤 추천 화면에서 추가로 입력해 주세요."
)
RECOMMEND_FOLLOW_UP_LIMIT_REACHED = (
    "추가 확인은 여기서 멈추고, 지금 입력된 정보 기준으로 볼 수 있는 정책을 넓게 안내할게요. "
    "일부 조건은 '잘 모르겠어요'로 반영되어 정확한 우선순위는 낮을 수 있어요."
)
RECOMMEND_FALLBACK_ERROR = (
    "맞춤 추천을 만드는 중에 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)
RECOMMEND_MAX_RETRIES = 1

ELIGIBILITY_SOURCE_TYPE = "CHAT"
ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS = 60
ELIGIBILITY_LOCK_TIMEOUT = "5s"
ELIGIBILITY_STATEMENT_TIMEOUT = "60s"
ELIGIBILITY_CLARIFICATION_FALLBACK = (
    "어떤 정책의 지원 가능성을 확인하고 싶으신가요? 정책명을 알려주시면 조건을 기준으로 분석해 드릴게요."
)
ELIGIBILITY_FALLBACK_FOLLOW_UP = (
    "지원 가능성을 판단하려면 정보가 조금 더 필요해요. 지원 가능성 분석 화면에서 추가 정보를 입력해 주세요."
)
ELIGIBILITY_FALLBACK_ERROR = (
    "지원 가능성을 분석하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)

COMPARE_CLARIFICATION_FALLBACK = (
    "비교할 정책 2개를 알려주세요. 예를 들어 '농식품바우처와 건강보험 임신출산 진료비를 비교해줘'처럼 질문하면 조건 기준으로 비교해 드릴게요."
)
COMPARE_FALLBACK_ERROR = (
    "정책 비교 결과를 만드는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)

APPLY_CLARIFICATION_FALLBACK = (
    "어떤 정책의 신청 방법을 알고 싶으신가요? 정책명을 알려주시면 신청 방법과 준비서류를 안내해 드릴게요."
)
APPLY_LIFECYCLE_TIMEOUT_SECONDS = 12
APPLY_LOCK_TIMEOUT = "5s"
APPLY_STATEMENT_TIMEOUT = "10s"
APPLY_CHECKLIST_PREVIEW = 5
APPLY_MAX_RETRIES = 1
APPLY_TEMPORARY_FAILURE_FALLBACK = (
    "신청 안내를 준비하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)

EVIDENCE_ROLE_ENUM: frozenset[str] = frozenset(
    {"SUMMARY", "TARGET", "BENEFIT", "APPLICATION", "CAUTION"}
)

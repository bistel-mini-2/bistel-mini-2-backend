from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.ai.states.chat_state import (
    ChatGraphState,
    ChatSlot,
    HistoryMessage,
    Intent,
    PendingState,
    ProfileSlot,
    RecentAssistantPolicy,
    SlotPolicy,
)
from app.ai.nodes.chat.slots import (
    CHILD_AGE_TO_STAGE as _CHILD_AGE_TO_STAGE,
    CONFIRM_NO_HINTS as _CONFIRM_NO_HINTS,
    CONFIRM_OPTIONS as _CONFIRM_OPTIONS,
    CONFIRM_YES_HINTS as _CONFIRM_YES_HINTS,
    INCOME_BRACKET_TO_PROFILE_CODE as _INCOME_BRACKET_TO_PROFILE_CODE,
    PROFILE_LABELS as _PROFILE_LABELS,
    PROFILE_OPTION_LABELS as _PROFILE_OPTION_LABELS,
    RECOMMEND_WIZARD_FIELDS,
    REQUIRED_SLOTS,
    SLOT_LABELS as _SLOT_LABELS,
    SLOT_OPTIONS as _SLOT_OPTIONS,
    SLOT_QUESTIONS as _SLOT_QUESTIONS,
)
from app.ai.nodes.chat.prompts import (
    APPLICATION_PERIOD_CONTEXT_RULES as _APPLICATION_PERIOD_CONTEXT_RULES,
    ASSERTIVE_PHRASES as _ASSERTIVE_PHRASES,
    COMMON_SAFETY_RULES as _COMMON_SAFETY_RULES,
    BRANCH_SYSTEM_PROMPTS as _BRANCH_SYSTEM_PROMPTS,
)
from app.ai.nodes.chat.constants import (
    APPLY_CHECKLIST_PREVIEW as _APPLY_CHECKLIST_PREVIEW,
    APPLY_CLARIFICATION_FALLBACK as _APPLY_CLARIFICATION_FALLBACK,
    APPLY_LIFECYCLE_TIMEOUT_SECONDS as _APPLY_LIFECYCLE_TIMEOUT_SECONDS,
    APPLY_LOCK_TIMEOUT as _APPLY_LOCK_TIMEOUT,
    APPLY_MAX_RETRIES as _APPLY_MAX_RETRIES,
    APPLY_STATEMENT_TIMEOUT as _APPLY_STATEMENT_TIMEOUT,
    APPLY_TEMPORARY_FAILURE_FALLBACK as _APPLY_TEMPORARY_FAILURE_FALLBACK,
    COMPARE_CLARIFICATION_FALLBACK as _COMPARE_CLARIFICATION_FALLBACK,
    COMPARE_FALLBACK_ERROR as _COMPARE_FALLBACK_ERROR,
    ELIGIBILITY_CLARIFICATION_FALLBACK as _ELIGIBILITY_CLARIFICATION_FALLBACK,
    ELIGIBILITY_FALLBACK_ERROR as _ELIGIBILITY_FALLBACK_ERROR,
    ELIGIBILITY_FALLBACK_FOLLOW_UP as _ELIGIBILITY_FALLBACK_FOLLOW_UP,
    ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS as _ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS,
    ELIGIBILITY_LOCK_TIMEOUT as _ELIGIBILITY_LOCK_TIMEOUT,
    ELIGIBILITY_SOURCE_TYPE as _ELIGIBILITY_SOURCE_TYPE,
    ELIGIBILITY_STATEMENT_TIMEOUT as _ELIGIBILITY_STATEMENT_TIMEOUT,
    EVIDENCES_MAX as _EVIDENCES_MAX,
    EVIDENCE_ROLE_ENUM as _EVIDENCE_ROLE_ENUM,
    INTENT_TO_ACTION_TYPE as _INTENT_TO_ACTION_TYPE,
    INTENT_TO_API_ACTION as _INTENT_TO_API_ACTION,
    LLM_MODEL as _LLM_MODEL,
    POLICIES_MAX as _POLICIES_MAX,
    RAG_TOP_K as _RAG_TOP_K,
    RECOMMEND_FALLBACK_ERROR as _RECOMMEND_FALLBACK_ERROR,
    RECOMMEND_FALLBACK_FOLLOW_UP as _RECOMMEND_FALLBACK_FOLLOW_UP,
    RECOMMEND_FOLLOW_UP_LIMIT_REACHED as _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
    RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS as _RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS,
    RECOMMEND_LOCK_TIMEOUT as _RECOMMEND_LOCK_TIMEOUT,
    RECOMMEND_MAX_RETRIES as _RECOMMEND_MAX_RETRIES,
    RECOMMEND_SOURCE_TYPE as _RECOMMEND_SOURCE_TYPE,
    RECOMMEND_STATEMENT_TIMEOUT as _RECOMMEND_STATEMENT_TIMEOUT,
    SNIPPET_LIMIT as _SNIPPET_LIMIT,
)
from app.ai.utils.policy_summary_utils import build_policy_summary_key_points
from app.common.ai_status import RequestStatus
from app.common.exceptions import AppException, ErrorCode
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.repositories.family_profile_repository import FamilyProfileRepository
from app.repositories.policy_repository import PolicyRepository
from app.schemas.ai_contract import EvidenceChunk
from app.schemas.ai_request_schema import AiRequestSnapshot
from app.schemas.apply_schema import ApplyPreparationResponse
from app.services.apply_preparation_service import ApplyPreparationService
from app.services.policy_rag_service import PolicyRagService

if TYPE_CHECKING:
    from app.ai.graphs.comparison_graph import ComparisonGraphRunner
    from app.ai.graphs.eligibility_graph import EligibilityGraphRunner
    from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def _lifecycle_service_class() -> type["AiRequestLifecycleService"]:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService

    return AiRequestLifecycleService


logger = logging.getLogger(__name__)


def _filled_slots(profile: ProfileSlot | None) -> set[str]:
    if not profile:
        return set()
    filled: set[str] = set()
    for key in ("stage", "child_age", "income", "region"):
        if profile.get(key):
            filled.add(key)
    if profile.get("special"):
        filled.add("special")
    return filled


def _missing_required(intent: Intent, profile: ProfileSlot | None) -> list[str]:
    required = REQUIRED_SLOTS.get(intent, ())
    filled = _filled_slots(profile)
    # 사용자가 한 번 "건너뛰기"한 슬롯은 다시 묻지 않는다.
    skipped = set((profile or {}).get("skipped") or [])
    return [s for s in required if s not in filled and s not in skipped]


def _merge_profile(
    base: ProfileSlot | None, extracted: dict[str, Any] | None
) -> ProfileSlot:
    merged: dict[str, Any] = dict(base or {})
    if not extracted:
        return merged  # type: ignore[return-value]
    for key in ("stage", "child_age", "income", "region"):
        value = extracted.get(key)
        if value:
            merged[key] = value
    special = extracted.get("special")
    if special:
        existing = list(merged.get("special") or [])
        for item in special:
            if item and item not in existing:
                existing.append(item)
        merged["special"] = existing
    return merged  # type: ignore[return-value]


def _format_profile_context(profile: ProfileSlot | None) -> str:
    if not profile:
        return "(없음)"
    parts: list[str] = []
    for key, label in _SLOT_LABELS.items():
        if key == "special":
            continue
        if profile.get(key):
            parts.append(f"- {label}: {_profile_value_label(key, profile[key])}")
    if profile.get("special"):
        parts.append(f"- 특이사항: {_profile_value_label('special', profile['special'])}")
    return "\n".join(parts) if parts else "(없음)"


def _build_slot_question(awaiting: list[str], profile: ProfileSlot | None) -> str:
    if not awaiting:
        return "조금 더 알려주시면 맞춤으로 찾아드릴게요."
    first = awaiting[0]
    question = _SLOT_QUESTIONS.get(first, f"{first}을(를) 알려주세요.")
    if len(awaiting) == 1:
        return f"맞춤 추천을 위해 하나만 여쭤볼게요. {question}"
    rest = ", ".join(_SLOT_LABELS.get(slot, slot) for slot in awaiting[1:])
    return f"맞춤 추천을 위해 몇 가지만 여쭤볼게요. 먼저 {question} (이어서 {rest}도 확인할게요.)"


def _profile_to_selected_conditions(profile: ProfileSlot | None) -> dict[str, Any]:
    """수집한 프로필(코드값)을 추천 엔진의 selected_conditions 형태로 변환.
    키는 ConditionAgent 가 읽는 이름(stage/childAge/income/region/special)에 맞춘다.
    skipped/None/빈 값은 제외한다.
    """
    if not profile:
        return {}
    out: dict[str, Any] = {}
    stage = profile.get("stage")
    child_age = profile.get("child_age")
    if child_age:
        out["childAge"] = child_age
        # child_age 는 직접 필터가 아니므로 stage 로 파생(명시 stage 가 없을 때).
        if not stage:
            stage = _CHILD_AGE_TO_STAGE.get(child_age)
    if stage:
        out["stage"] = stage
    if profile.get("income"):
        out["income"] = profile["income"]
    if profile.get("region"):
        out["region"] = profile["region"]
    special = profile.get("special")
    if special:
        out["special"] = [s for s in special if s]
    return out


def _profile_value_label(key: str, value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "해당 없음"
        return ", ".join(_profile_value_label(key, item) for item in value)
    text = str(value)
    if key == "income" and text in _INCOME_BRACKET_TO_PROFILE_CODE:
        text = _INCOME_BRACKET_TO_PROFILE_CODE[text]
    return _PROFILE_OPTION_LABELS.get(key, {}).get(text, text)


def _profile_summary_from_snapshot(
    snap: dict[str, Any],
    *,
    income_bracket: str | None = None,
    region_code: str | None = None,
    household_type: str | None = None,
    pregnancy_status: bool = False,
) -> list[str] | None:
    stage = snap.get("stage") or snap.get("life_stage")
    child_age = snap.get("childAge") or snap.get("child_age")
    income = snap.get("income") or snap.get("income_level")
    region = snap.get("region") or snap.get("region_code")
    special = snap.get("special")

    if not stage and pregnancy_status:
        stage = "pregnant"
    if not income and income_bracket:
        income = income_bracket
    if not region and region_code:
        region = region_code
    if special is None and household_type:
        special = [household_type]

    parts: list[str] = []
    rows = (
        ("stage", stage),
        ("child_age", child_age),
        ("income", income),
        ("region", region),
        ("special", special),
    )
    for key, value in rows:
        if value is None or value == "":
            continue
        parts.append(
            f"{_PROFILE_LABELS[key]}: {_profile_value_label(key, value)}"
        )

    return parts or None


def _interpret_confirm(text: str | None) -> str | None:
    """확인 프롬프트에 대한 사용자 응답을 yes/no/None 으로 해석."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(k in t for k in _CONFIRM_NO_HINTS):
        return "no"
    if any(k in t for k in _CONFIRM_YES_HINTS):
        return "yes"
    return None


def _recommend_follow_up_already_asked(slot: ChatSlot | None) -> bool:
    pending = (slot or {}).get("pending") or {}
    return pending.get("intent") == "recommend" and pending.get("kind") == "slot"


async def _load_db_profile_summary(user_id: int) -> list[str] | None:
    """저장된 회원 프로필을 사람이 읽는 요약 리스트로. 의미있는 값이 없으면 None."""
    try:
        async with AsyncSessionLocal() as db:
            profile = await FamilyProfileRepository.find_profile_by_user_id(db, user_id)
    except Exception:
        logger.exception("failed to load user profile for confirm prompt")
        return None
    if profile is None:
        return None
    snap = dict(profile.profile_json or {})
    return _profile_summary_from_snapshot(
        snap,
        income_bracket=profile.income_bracket,
        region_code=profile.region_code,
        household_type=profile.household_type,
        pregnancy_status=getattr(profile, "pregnancy_status", False),
    )


def _detect_assertive_phrases(content: str) -> list[str]:
    return [phrase for phrase in _ASSERTIVE_PHRASES if phrase in content]


def _normalize_evidence_role(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    upper = value.upper()
    return upper if upper in _EVIDENCE_ROLE_ENUM else None


class _ExtractedProfile(BaseModel):
    stage: str | None = None
    child_age: str | None = None
    income: str | None = None
    region: str | None = None
    special: list[str] | None = None


class _IntentDecision(BaseModel):
    intent: Intent = Field(description="사용자 메시지의 의도 분류")
    resolved_policy_slug: str | None = Field(
        default=None,
        description="사용자가 직전 거론 정책을 지시어로 가리키는 경우 그 정책의 slug. 그렇지 않으면 null.",
    )
    extracted_profile: _ExtractedProfile | None = Field(
        default=None,
        description="이번 메시지에서 새로 드러난 사용자 조건. 없으면 null.",
    )


BRANCH_LLM_TAG = "chat_branch_llm"
_BRANCH_LLM_CONFIG = {"tags": [BRANCH_LLM_TAG]}
_BRANCH_TOKEN_CALLBACK: ContextVar[
    Callable[[str], Awaitable[None]] | None
] = ContextVar("chat_branch_token_callback", default=None)


def set_branch_token_callback(
    callback: Callable[[str], Awaitable[None]] | None,
):
    return _BRANCH_TOKEN_CALLBACK.set(callback)


def reset_branch_token_callback(token) -> None:
    _BRANCH_TOKEN_CALLBACK.reset(token)


def _llm() -> ChatOpenAI:
    kwargs: dict = {"model": _LLM_MODEL, "temperature": 0.2}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


async def _mark_recommendation_failed(request_id: int, error_message: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            await _lifecycle_service_class()().mark_failed(
                db=db,
                request_type="recommendation",
                request_id=request_id,
                error_message=error_message,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "failed to mark recommendation request as failed: %s", request_id
            )


def _adapt_recommendation_result(
    result_json: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results = result_json.get("results") or []
    policies: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    seen_chunks: set[Any] = set()
    for item in results[:_POLICIES_MAX]:
        slug = item.get("slug") or item.get("policy_code") or item.get("policy_id")
        if slug:
            policies.append(
                {
                    "policy_id": item.get("policy_id") or slug,
                    "slug": slug,
                    "policy_name": item.get("policy_name") or "",
                    "summary": item.get("summary") or item.get("benefit_summary"),
                    "tag": None,
                    "tagTone": None,
                }
            )
        for evidence in item.get("evidence") or []:
            chunk_id = evidence.get("chunk_id")
            if chunk_id is None or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            evidences.append(
                {
                    "chunk_id": chunk_id,
                    "snippet": (evidence.get("snippet") or "")[:_SNIPPET_LIMIT],
                    "source_title": evidence.get("source_title")
                    or item.get("policy_name")
                    or "",
                    "source_url": evidence.get("source_url"),
                    "evidence_role": _normalize_evidence_role(
                        evidence.get("evidence_role")
                    ),
                }
            )
            if len(evidences) >= _EVIDENCES_MAX:
                return policies, evidences
    return policies, evidences


def _adapt_eligibility_result(
    result_json: dict[str, Any],
    *,
    fallback_slug: str,
    fallback_policy_name: str | None,
) -> tuple[str, str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    slug = str(result_json.get("slug") or fallback_slug)
    policy_name = str(result_json.get("policy_name") or fallback_policy_name or "")
    user_status = result_json.get("user_status")
    summary = result_json.get("summary")
    questions = result_json.get("follow_up_questions") or result_json.get("questions") or []

    if result_json.get("status") == RequestStatus.FOLLOW_UP_REQUIRED.value:
        if questions:
            first_question = questions[0]
            question_text = (
                first_question.get("question_text")
                if isinstance(first_question, dict)
                else None
            )
            content = question_text or _ELIGIBILITY_FALLBACK_FOLLOW_UP
        else:
            content = _ELIGIBILITY_FALLBACK_FOLLOW_UP
    elif summary:
        content = str(summary)
    elif user_status:
        content = (
            f"{policy_name} 지원 가능성 분석이 완료됐어요. "
            "자세한 조건은 근거와 함께 확인해 주세요."
        )
    else:
        content = _ELIGIBILITY_FALLBACK_ERROR

    policies = [
        {
            "policy_id": result_json.get("policy_id") or slug,
            "slug": slug,
            "policy_name": policy_name,
            "summary": summary,
            "tag": None,
            "tagTone": None,
        }
    ]
    evidences: list[dict[str, Any]] = []
    seen_chunks: set[Any] = set()
    for evidence in result_json.get("evidences") or []:
        if not isinstance(evidence, dict):
            continue
        chunk_id = evidence.get("chunk_id")
        if chunk_id in (None, "") or chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        evidences.append(
            {
                "chunk_id": chunk_id,
                "snippet": str(evidence.get("snippet") or "")[:_SNIPPET_LIMIT],
                "source_title": evidence.get("source_title") or policy_name,
                "source_url": evidence.get("source_url"),
                "evidence_role": _normalize_evidence_role(
                    evidence.get("evidence_role")
                ),
            }
        )
        if len(evidences) >= _EVIDENCES_MAX:
            break

    return content, user_status, policies, evidences


def _adapt_comparison_result(
    result_json: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    policy_a = result_json.get("policy_a") or {}
    policy_b = result_json.get("policy_b") or {}
    name_a = str(policy_a.get("name") or policy_a.get("slug") or "첫 번째 정책")
    name_b = str(policy_b.get("name") or policy_b.get("slug") or "두 번째 정책")
    selection_guide = str(result_json.get("selection_guide") or "")
    diff_table = [
        item for item in result_json.get("diff_table") or []
        if isinstance(item, dict)
    ]
    highlights: list[str] = []
    for item in diff_table[:3]:
        field = item.get("field")
        a_value = str(item.get("a") or "공식 안내 확인 필요")
        b_value = str(item.get("b") or "공식 안내 확인 필요")
        if field:
            highlights.append(f"- {field}: {name_a}은 {a_value}, {name_b}은 {b_value}")

    content_parts = [
        f"{name_a}와 {name_b}를 조건 기준으로 비교했어요.",
    ]
    if selection_guide:
        content_parts.append(selection_guide)
    if highlights:
        content_parts.append("주요 차이는 다음과 같아요.\n" + "\n".join(highlights))
    content_parts.append("자세한 항목별 비교는 정책 비교 화면에서 이어서 확인할 수 있어요.")

    policies = []
    for policy in (policy_a, policy_b):
        slug = policy.get("slug")
        if not slug:
            continue
        policies.append(
            {
                "policy_id": policy.get("policy_id") or slug,
                "slug": slug,
                "policy_name": policy.get("name") or "",
                "summary": (policy.get("summary") or {}).get("condition"),
                "tag": None,
                "tagTone": None,
            }
        )
    return "\n\n".join(content_parts), policies


def _pick_compare_targets(
    policies: list[dict[str, Any]],
    *,
    slot: ChatSlot | None,
    user_content: str,
) -> tuple[tuple[str, str | None] | None, tuple[str, str | None] | None]:
    candidates: list[tuple[str, str | None]] = []
    seen: set[str] = set()

    def append(slug: Any, policy_name: Any = None) -> None:
        if not slug:
            return
        key = str(slug)
        if key in seen:
            return
        seen.add(key)
        candidates.append((key, str(policy_name) if policy_name else None))

    mentioned_slot_policies: list[SlotPolicy] = []
    for policy in (slot or {}).get("recent_policies") or []:
        if _user_mentions_policy_name(user_content, policy.get("policy_name")):
            mentioned_slot_policies.append(policy)  # type: ignore[arg-type]
    for policy in mentioned_slot_policies:
        append(policy.get("slug"), policy.get("policy_name"))

    if not candidates and _is_context_dependent_compare_question(user_content):
        for policy in (slot or {}).get("recent_policies") or []:
            append(policy.get("slug"), policy.get("policy_name"))
            if len(candidates) >= 2:
                break

    for policy in policies:
        append(policy.get("slug"), policy.get("policy_name"))
        if len(candidates) >= 2:
            break

    if len(candidates) < 2:
        return None, None
    return candidates[0], candidates[1]


def _evidence_chunk_to_chat_evidence(
    chunk: EvidenceChunk | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(chunk, EvidenceChunk):
        item = chunk.model_dump()
    else:
        item = dict(chunk)
    return {
        "chunk_id": item.get("chunk_id"),
        "snippet": str(item.get("snippet") or "")[:_SNIPPET_LIMIT],
        "source_title": item.get("source_title"),
        "source_url": item.get("source_url"),
        "evidence_role": _normalize_evidence_role(item.get("evidence_role")),
    }


def _adapt_policy_summary_result(
    result: dict[str, Any],
    *,
    policy: dict[str, Any],
    fallback_evidences: list[dict[str, Any]],
) -> tuple[
    str,
    str,
    list[dict[str, str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    easy_summary = str(
        result.get("easy_summary")
        or result.get("summary")
        or policy.get("easy_summary")
        or policy.get("summary")
        or ""
    ).strip()
    key_points = [
        {
            "label": str(item.get("label") or ""),
            "content": str(item.get("content") or ""),
        }
        for item in result.get("key_points") or []
        if isinstance(item, dict) and item.get("content")
    ]
    if not key_points:
        key_points = build_policy_summary_key_points(
            policy,
            content_limit=_SNIPPET_LIMIT,
        )

    evidences = [
        _evidence_chunk_to_chat_evidence(chunk)
        for chunk in result.get("evidence_chunks") or []
        if isinstance(chunk, (EvidenceChunk, dict))
    ]
    if not evidences:
        evidences = fallback_evidences

    policy_card = {
        "policy_id": policy.get("policy_id") or policy.get("slug"),
        "slug": policy.get("slug"),
        "policy_name": policy.get("name") or policy.get("policy_name") or "",
        "summary": easy_summary or None,
        "tag": None,
        "tagTone": None,
    }
    content = easy_summary or _policy_summary_fallback_content(policy)
    return content, easy_summary, key_points[:3], [policy_card], evidences[:_EVIDENCES_MAX]

def _policy_summary_fallback_content(policy: dict[str, Any]) -> str:
    name = policy.get("name") or policy.get("policy_name") or "policy"
    return f"{name} summary is not available yet."


def _attach_recommendation_context(
    policies: list[dict[str, Any]],
    *,
    request_id: str,
    selected_conditions: dict[str, Any],
    merged_condition_json: dict[str, Any],
) -> list[dict[str, Any]]:
    if not policies:
        return policies
    return [
        {
            **policy,
            "recommendation_request_id": request_id,
            "source_ref_id": request_id,
            "selected_conditions": selected_conditions,
            "merged_condition_json": merged_condition_json,
        }
        for policy in policies
    ]


def _pick_apply_target(
    policies: list[dict[str, Any]],
    *,
    user_content: str | None = None,
    require_policy_name_mention: bool = False,
) -> tuple[str | None, str | None]:
    for policy in policies:
        slug = policy.get("slug")
        if not slug:
            continue
        policy_name = policy.get("policy_name") or None
        if require_policy_name_mention and not _user_mentions_policy_name(
            user_content or "", policy_name
        ):
            continue
        return str(slug), policy_name
    return None, None


def _normalize_policy_mention_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", value).lower()


def _user_mentions_policy_name(user_content: str, policy_name: str | None) -> bool:
    normalized_policy_name = _normalize_policy_mention_text(policy_name)
    if len(normalized_policy_name) < 2:
        return False
    normalized_user_content = _normalize_policy_mention_text(user_content)
    return normalized_policy_name in normalized_user_content


_CONTEXT_DEPENDENT_APPLY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(이거|그거|그 정책|방금|위 정책|앞(?:에서)? 말한|아까|해당 정책)"),
    re.compile(r"(신청|서류|준비|기간|어디서|어떻게|방법|절차|문의).*[?？]?$"),
)

_CONTEXT_DEPENDENT_COMPARE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(둘|두 정책|두개|2개|서로|비교|차이|뭐가 더|어느 쪽|어떤 게)"),
)


def _is_context_dependent_apply_question(user_content: str) -> bool:
    content = user_content.strip()
    if not content:
        return False
    return any(pattern.search(content) for pattern in _CONTEXT_DEPENDENT_APPLY_PATTERNS)


def _is_context_dependent_compare_question(user_content: str) -> bool:
    content = user_content.strip()
    if not content:
        return False
    return any(
        pattern.search(content) for pattern in _CONTEXT_DEPENDENT_COMPARE_PATTERNS
    )


def _summary_mode(user_content: str | None) -> str:
    content = user_content or ""
    if any(token in content for token in ("내 상황", "나 기준", "맞춤", "개인")):
        return "personalized"
    if any(token in content for token in ("체크", "준비", "할 일")):
        return "checklist"
    if any(token in content for token in ("짧게", "간단", "한줄", "핵심만")):
        return "short"
    return "plain"


def _summary_target_type(
    user_content: str | None,
    slot: ChatSlot | None,
    resolved_slug: str | None = None,
) -> str | None:
    content = user_content or ""
    if resolved_slug:
        return "policy"
    if any(token in content for token in ("추천", "방금 추천", "추천한")):
        return "recommendation_result"
    if any(token in content for token in ("지원가능성", "지원 가능성", "가능성 분석", "자격")):
        return "eligibility_result"
    if any(token in content for token in ("신청", "서류", "준비")):
        return "application_guide"
    if any(token in content for token in ("비교", "차이")):
        return "comparison_result"
    if any(token in content for token in ("이 정책", "그 정책", "정책 요약", "정책")):
        return "policy"
    recent = (slot or {}).get("recent_policies") or []
    if len(recent) == 1:
        return "policy"
    return None


def _summary_target_id(
    slot: ChatSlot | None,
    resolved_slug: str | None = None,
) -> str | None:
    if resolved_slug:
        return resolved_slug
    recent = (slot or {}).get("recent_policies") or []
    if len(recent) == 1 and recent[0].get("slug"):
        return str(recent[0]["slug"])
    return None


def _recent_assistant_policy_target(
    policy: RecentAssistantPolicy | None,
) -> tuple[str | None, str | None]:
    if not policy:
        return None, None
    slug = policy.get("slug")
    if not slug:
        return None, None
    return str(slug), policy.get("policy_name") or None


def _build_apply_card(
    apply_response: ApplyPreparationResponse, policy_name: str | None
) -> dict[str, Any]:
    checklist = [
        {"id": item.id, "label": item.label, "done": item.done}
        for item in apply_response.checklist[:_APPLY_CHECKLIST_PREVIEW]
    ]
    return {
        "policy_id": apply_response.policy_id,
        "policy_name": policy_name or apply_response.policy_id,
        "how_to_apply": apply_response.how_to_apply,
        "contact": apply_response.contact,
        "official_url": apply_response.official_url,
        "checklist": checklist,
        "caution": apply_response.caution,
    }


def _format_apply_card_context(apply_card: dict[str, Any]) -> str:
    parts: list[str] = []
    name = apply_card.get("policy_name")
    if name:
        parts.append(f"- 정책명: {name}")
    if apply_card.get("how_to_apply"):
        parts.append(f"- 신청 방법: {apply_card['how_to_apply']}")
    if apply_card.get("contact"):
        parts.append(f"- 문의처: {apply_card['contact']}")
    if apply_card.get("official_url"):
        parts.append(f"- 공식 안내: {apply_card['official_url']}")
    checklist = apply_card.get("checklist") or []
    if checklist:
        items = "; ".join(item["label"] for item in checklist if item.get("label"))
        if items:
            parts.append(f"- 체크리스트: {items}")
    if apply_card.get("caution"):
        parts.append(f"- 주의사항: {apply_card['caution']}")
    return "\n".join(parts)


def _format_application_period_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""

    parts: list[str] = []
    field_labels = {
        "application_status": "신청 상태",
        "application_period_text": "신청 기간 텍스트",
        "application_start_date": "신청 시작일",
        "application_end_date": "신청 종료일",
        "deadline": "마감일",
        "source_text": "조건/원문 source_text",
        "source_fields": "source_fields",
    }
    for field, label in field_labels.items():
        value = context.get(field)
        if value in (None, "", []):
            continue
        if isinstance(value, list):
            value = ", ".join(str(item) for item in value if item)
        parts.append(f"- {label}: {value}")

    return "\n".join(parts)


def _format_slot_context(slot: ChatSlot | None) -> str:
    if not slot:
        return "(없음)"
    recent = slot.get("recent_policies") or []
    if not recent:
        return "(없음)"
    return "\n".join(
        f"- {p.get('policy_name') or '(이름 없음)'} "
        f"(slug={p.get('slug')}, action={p.get('last_action')})"
        for p in recent
    )


def _find_slot_policy_by_slug(
    slot: ChatSlot | None, slug: str
) -> SlotPolicy | None:
    if not slot or not slug:
        return None
    for p in slot.get("recent_policies") or []:
        if p.get("slug") == slug:
            return p  # type: ignore[return-value]
    return None


def _history_to_lc_messages(history: list[HistoryMessage]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for entry in history:
        content = entry.get("content")
        if not content:
            continue
        if entry.get("role") == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


class ChatGraphNodes:
    def __init__(
        self,
        rag_service: PolicyRagService | None = None,
        lifecycle_service: AiRequestLifecycleService | None = None,
        eligibility_graph: EligibilityGraphRunner | None = None,
        comparison_graph: ComparisonGraphRunner | None = None,
        policy_summary_graph: PolicySummaryGraphRunner | None = None,
    ) -> None:
        self.rag_service = rag_service or PolicyRagService()
        self.lifecycle_service = lifecycle_service
        self.eligibility_graph = eligibility_graph
        self.comparison_graph = comparison_graph
        self.policy_summary_graph = policy_summary_graph

    async def branch_recommend(self, state: ChatGraphState) -> ChatGraphState:
        selected_conditions = _profile_to_selected_conditions(state.get("profile"))
        follow_up_already_asked = _recommend_follow_up_already_asked(
            state.get("slot")
        ) or bool((state.get("profile") or {}).get("db_profile_confirmed"))
        snapshot, lifecycle_error = await self._run_recommendation_lifecycle(
            user_id=state["user_id"],
            user_content=state["user_content"],
            selected_conditions=selected_conditions or None,
            follow_up_resolved=follow_up_already_asked,
        )
        if lifecycle_error == "temporary_failure":
            return {
                **state,
                "recommend_flow_status": "retryable_error",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "recommend_max_retries": _RECOMMEND_MAX_RETRIES,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot is None:
            return {
                **state,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot.status == RequestStatus.FOLLOW_UP_REQUIRED:
            if follow_up_already_asked:
                return {
                    **state,
                    "recommend_flow_status": "fallback_needed",
                    "recommend_error_message": _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
                    "branch_content": _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
                    "branch_policies": [],
                    "branch_evidences": [],
                    "branch_apply_card": None,
                    "slot_request": None,
                    "pending": None,
                }
            questions = snapshot.questions or []
            if questions:
                awaiting = [
                    str(question.get("field_name") or f"follow_up_{index + 1}")
                    for index, question in enumerate(questions)
                ]
                slot_request = {
                    "flow_type": "recommend",
                    "request_id": snapshot.request_id,
                    "target_policy_id": None,
                    "source_type": "CHAT",
                    "source_ref_id": snapshot.request_id,
                    "target_type": None,
                    "summary_mode": None,
                    "current": awaiting[0] if awaiting else None,
                    "awaiting": awaiting,
                    "multi": ["special"],
                    "fields": [
                        {
                            "key": key,
                            "label": (
                                question.get("question_text")
                                or _SLOT_LABELS.get(key, key)
                            ),
                            "options": _SLOT_OPTIONS.get(key, []),
                        }
                        for key, question in zip(awaiting, questions, strict=False)
                    ],
                }
                pending: PendingState = {
                    "intent": "recommend",
                    "awaiting": awaiting,
                    "asked": [awaiting[0]] if awaiting else [],
                    "kind": "slot",
                }
                return {
                    **state,
                    "recommend_flow_status": "ok",
                    "recommend_error_message": None,
                    "branch_content": "맞춤 추천을 위해 정보가 조금 더 필요해요.",
                    "branch_policies": [],
                    "branch_evidences": [],
                    "branch_apply_card": None,
                    "slot_request": slot_request,
                    "pending": pending,
                }
            return {
                **state,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_content": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot.status != RequestStatus.COMPLETED:
            return {
                **state,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        policies, evidences = _adapt_recommendation_result(snapshot.result_json)
        policies = _attach_recommendation_context(
            policies,
            request_id=snapshot.request_id,
            selected_conditions=selected_conditions,
            merged_condition_json=snapshot.merged_condition_json or selected_conditions,
        )
        content = await self._generate_branch_answer("recommend", state, evidences)
        return {
            **state,
            "recommend_flow_status": "ok",
            "recommend_error_message": None,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    async def recommend_retry_increment(self, state: ChatGraphState) -> ChatGraphState:
        retry_count = int(state.get("recommend_retry_count") or 0) + 1
        return {**state, "recommend_retry_count": retry_count}

    async def recommend_fallback_build(self, state: ChatGraphState) -> ChatGraphState:
        fallback_message = state.get("recommend_error_message") or _RECOMMEND_FALLBACK_ERROR
        return {
            **state,
            "recommend_flow_status": "ok",
            "branch_content": fallback_message,
            "branch_policies": [],
            "branch_evidences": [],
            "branch_apply_card": None,
        }

    async def branch_eligibility(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        if resolved_slug:
            slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug)
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "eligibility", "slot_used": True, "rag_skipped": True},
            )
            return await self._run_eligibility_branch(
                state=state,
                policy_slug=resolved_slug,
                policy_name=(slot_policy or {}).get("policy_name"),
                evidences=[],
            )
        logger.info(
            "chat_slot_resolved",
            extra={"intent": "eligibility", "slot_used": False, "rag_skipped": False},
        )
        policies, evidences = await self._rag_lookup(state["user_content"])
        slug, policy_name = _pick_apply_target(
            policies,
            user_content=state["user_content"],
            require_policy_name_mention=True,
        )
        if slug is None:
            slug, policy_name = _recent_assistant_policy_target(
                state.get("recent_assistant_policy")
            )
        if slug is None:
            return {
                **state,
                "branch_content": _ELIGIBILITY_CLARIFICATION_FALLBACK,
                "branch_user_status": None,
                "branch_policies": [],
                "branch_evidences": evidences,
            }
        return await self._run_eligibility_branch(
            state=state,
            policy_slug=slug,
            policy_name=policy_name,
            evidences=evidences,
        )

    async def branch_compare(self, state: ChatGraphState) -> ChatGraphState:
        policies, evidences = await self._rag_lookup(state["user_content"])
        first, second = _pick_compare_targets(
            policies,
            slot=state.get("slot"),
            user_content=state["user_content"],
        )
        if first is None or second is None:
            return {
                **state,
                "branch_content": _COMPARE_CLARIFICATION_FALLBACK,
                "branch_policies": [],
                "branch_evidences": evidences,
            }
        return await self._run_comparison_branch(
            state=state,
            slug_a=first[0],
            slug_b=second[0],
            evidences=evidences,
        )

    async def branch_apply(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug or "")
        if resolved_slug and slot_policy:
            slug = resolved_slug
            policy_name = slot_policy.get("policy_name") or None
            evidences: list[dict[str, Any]] = []
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "apply", "slot_used": True, "rag_skipped": True},
            )
        else:
            policies, evidences = await self._rag_lookup(state["user_content"])
            slug, policy_name = _pick_apply_target(
                policies,
                user_content=state["user_content"],
                require_policy_name_mention=True,
            )
            if slug is None and _is_context_dependent_apply_question(
                state["user_content"]
            ):
                slug, policy_name = _recent_assistant_policy_target(
                    state.get("recent_assistant_policy")
                )
                if slug is not None:
                    evidences = []
                logger.info(
                    "chat_recent_assistant_policy_resolved",
                    extra={
                        "intent": "apply",
                        "recent_policy_used": slug is not None,
                        "rag_skipped": False,
                    },
                )
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "apply", "slot_used": False, "rag_skipped": False},
            )

        if slug is None:
            return {
                **state,
                "apply_flow_status": "fallback_needed",
                "apply_error_message": _APPLY_CLARIFICATION_FALLBACK,
                "branch_content": _APPLY_CLARIFICATION_FALLBACK,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }

        apply_response, apply_error = await self._run_apply_preparation(
            user_id=state["user_id"],
            policy_slug=slug,
        )
        if apply_error == "temporary_failure":
            return {
                **state,
                "apply_flow_status": "retryable_error",
                "apply_error_message": _APPLY_TEMPORARY_FAILURE_FALLBACK,
                "apply_max_retries": _APPLY_MAX_RETRIES,
                "branch_content": _APPLY_TEMPORARY_FAILURE_FALLBACK,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        if apply_response is None:
            content = await self._generate_branch_answer("apply", state, evidences)
            return {
                **state,
                "apply_flow_status": "ok",
                "apply_error_message": None,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        apply_card = _build_apply_card(apply_response, policy_name)
        application_period_context = await self._load_application_period_context(slug)
        apply_policies = [
            {
                "policy_id": None,
                "slug": slug,
                "policy_name": policy_name or "",
                "summary": None,
                "tag": None,
                "tagTone": None,
            }
        ]
        content = await self._generate_apply_answer(
            state,
            evidences,
            apply_card,
            application_period_context,
        )
        return {
            **state,
            "apply_flow_status": "ok",
            "apply_error_message": None,
            "branch_content": content,
            "branch_policies": apply_policies,
            "branch_evidences": evidences,
            "branch_apply_card": apply_card,
        }

    async def apply_retry_increment(self, state: ChatGraphState) -> ChatGraphState:
        retry_count = int(state.get("apply_retry_count") or 0) + 1
        return {**state, "apply_retry_count": retry_count}

    async def apply_fallback_build(self, state: ChatGraphState) -> ChatGraphState:
        fallback_message = (
            state.get("apply_error_message")
            or "신청 안내를 준비하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
        )
        return {
            **state,
            "apply_flow_status": "ok",
            "branch_content": fallback_message,
            "branch_policies": [],
            "branch_evidences": [],
            "branch_apply_card": None,
        }

    async def branch_policy_summary(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        fallback_evidences: list[dict[str, Any]] = []

        if resolved_slug:
            slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug)
            policy_slug = str(resolved_slug)
            policy_name = (slot_policy or {}).get("policy_name")
            logger.info(
                "chat_slot_resolved",
                extra={
                    "intent": "policy_summary",
                    "slot_used": True,
                    "rag_skipped": True,
                },
            )
        else:
            policies, fallback_evidences = await self._rag_lookup(state["user_content"])
            policy_slug, policy_name = _pick_apply_target(policies)
            logger.info(
                "chat_slot_resolved",
                extra={
                    "intent": "policy_summary",
                    "slot_used": False,
                    "rag_skipped": False,
                },
            )

        if not policy_slug:
            content = await self._generate_branch_answer(
                "policy_summary",
                state,
                fallback_evidences,
            )
            return {
                **state,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": fallback_evidences,
            }

        policy = await self._load_policy_detail(policy_slug)
        if policy is None:
            content = await self._generate_branch_answer(
                "policy_summary",
                state,
                fallback_evidences,
            )
            return {
                **state,
                "branch_content": content,
                "branch_policies": [
                    {
                        "policy_id": policy_slug,
                        "slug": policy_slug,
                        "policy_name": policy_name or "",
                        "summary": None,
                        "tag": None,
                        "tagTone": None,
                    }
                ],
                "branch_evidences": fallback_evidences,
            }

        try:
            summary_result = await self._policy_summary_graph().run(policy)
        except Exception:
            logger.exception("Policy summary graph failed; using RAG fallback")
            content = await self._generate_branch_answer(
                "policy_summary",
                state,
                fallback_evidences,
            )
            return {
                **state,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": fallback_evidences,
            }

        content, easy_summary, key_points, policies, evidences = (
            _adapt_policy_summary_result(
                summary_result,
                policy=policy,
                fallback_evidences=fallback_evidences,
            )
        )
        return {
            **state,
            "branch_content": content,
            "branch_easy_summary": easy_summary,
            "branch_key_points": key_points,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    async def branch_summary(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        target_type = _summary_target_type(
            state["user_content"],
            state.get("slot"),
            resolved_slug,
        )
        if target_type == "policy" and resolved_slug:
            slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug)
            policy_name = (slot_policy or {}).get("policy_name") or ""
            _, evidences = await self._rag_lookup(policy_name or state["user_content"])
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "summary", "slot_used": True, "rag_skipped": False},
            )
            policies = [
                {
                    "policy_id": None,
                    "slug": resolved_slug,
                    "policy_name": policy_name,
                    "summary": None,
                    "tag": None,
                    "tagTone": None,
                }
            ]
            content = await self._generate_branch_answer("summary", state, evidences)
            return {
                **state,
                "branch_content": content,
                "branch_policies": policies,
                "branch_evidences": evidences,
            }
        return await self._branch_with_rag("summary", state)

    async def branch_unclear(self, state: ChatGraphState) -> ChatGraphState:
        content = await self._generate_branch_answer("unclear", state, evidences=[])
        return {
            **state,
            "branch_content": content,
            "branch_policies": [],
            "branch_evidences": [],
        }

    async def collect_slots(self, state: ChatGraphState) -> ChatGraphState:
        """필수 슬롯이 부족하면 입력 위저드를 띄우고, intent를 pending으로 보류한다.

        추천은 여러 스텝(자녀나이→소득→가구특성)을 순서대로 내려보낸다. 게이트는
        필수(child_age)로 걸리지만, 폼에는 선택 슬롯까지 스텝으로 포함한다.
        """
        awaiting = state.get("awaiting_slots") or []
        profile: ProfileSlot = state.get("profile") or {}
        decision = state.get("supervisor_decision") or {}
        intent: Intent = decision.get("intent", "recommend")

        # 위저드 스텝: 추천이면 정해진 순서, 그 외 intent는 awaiting 그대로.
        # 이미 채운 슬롯은 스텝에서 제외한다.
        if intent == "recommend":
            filled = _filled_slots(profile)
            field_keys = [f for f in RECOMMEND_WIZARD_FIELDS if f not in filled]
            if not field_keys:
                field_keys = list(awaiting)
        else:
            field_keys = list(awaiting)

        prompt_awaiting = field_keys if intent == "recommend" else awaiting
        content = _build_slot_question(prompt_awaiting, profile)
        current = prompt_awaiting[0] if prompt_awaiting else None
        target_policy_id = decision.get("resolved_policy_slug")
        source_type = None
        source_ref_id = None
        if intent == "summary":
            source_type = _summary_target_type(
                state["user_content"],
                state.get("slot"),
                target_policy_id,
            )
            source_ref_id = _summary_target_id(state.get("slot"), target_policy_id)
        elif intent == "eligibility":
            source_type = "RECOMMENDATION_RESULT" if target_policy_id else "CHAT"
            source_ref_id = target_policy_id
        elif intent == "recommend":
            source_type = "CHAT"
        slot_request = {
            "flow_type": intent,
            "request_id": None,
            "target_policy_id": target_policy_id,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "target_type": source_type if intent == "summary" else None,
            "summary_mode": (
                _summary_mode(state["user_content"]) if intent == "summary" else None
            ),
            "current": current,
            "awaiting": field_keys,
            "multi": ["special"],  # 다중 선택 슬롯 힌트
            "fields": [
                {
                    "key": slot,
                    "label": _SLOT_LABELS.get(slot, slot),
                    "options": _SLOT_OPTIONS.get(slot, []),
                }
                for slot in field_keys
            ],
        }
        pending: PendingState = {
            "intent": intent,
            "awaiting": field_keys,
            "asked": [current] if current else [],
            "kind": "slot",
        }
        logger.info(
            "chat_slot_request",
            extra={"intent": intent, "awaiting": awaiting, "steps": field_keys},
        )
        return {
            **state,
            "branch_content": content,
            "branch_policies": [],
            "branch_evidences": [],
            "slot_request": slot_request,
            "pending": pending,
        }

    async def confirm_profile(self, state: ChatGraphState) -> ChatGraphState:
        """저장된 회원 프로필로 추천할지 한 번 확인(yes/no)한다."""
        pc = state.get("profile_confirm") or {}
        summary = pc.get("summary") or []
        lines = "\n".join(f"· {item}" for item in summary)
        content = (
            "저장된 정보로 맞춤 정책을 추천해드릴까요?\n"
            f"{lines}\n\n다른 조건으로 받고 싶으시면 다시 입력하실 수 있어요."
        )
        pending: PendingState = {
            "intent": "recommend",
            "awaiting": [],
            "asked": [],
            "kind": "confirm",
        }
        logger.info("chat_profile_confirm", extra={"fields": len(summary)})
        return {
            **state,
            "branch_content": content,
            "branch_policies": [],
            "branch_evidences": [],
            "profile_confirm": pc,
            "pending": pending,
        }

    async def assistant_payload_build(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": "missing"}
        intent: Intent = decision["intent"]
        slot_request = state.get("slot_request")
        profile_confirm = state.get("profile_confirm")
        is_prompt = bool(slot_request or profile_confirm)
        is_slot_request = bool(slot_request)
        api_action = None if is_prompt else _INTENT_TO_API_ACTION.get(intent)
        content = state.get("branch_content") or ""
        if intent != "unclear" and not is_prompt:
            assertive = _detect_assertive_phrases(content)
            if assertive:
                logger.warning(
                    "chat answer contains assertive phrases despite safety prompt",
                    extra={"intent": intent, "phrases": assertive},
                )
        payload = {
            "content": content,
            "user_status": state.get("branch_user_status"),
            "sources": [],
            "policies": state.get("branch_policies", []),
            "evidences": state.get("branch_evidences", []),
            "actions": [api_action] if api_action else [],
            "apply_card": state.get("branch_apply_card"),
            "easy_summary": state.get("branch_easy_summary"),
            "key_points": state.get("branch_key_points", []),
            "disclaimer": (intent != "unclear") and not is_prompt,
            # 슬롯/확인을 되묻는 중에는 면책 문구·확정 경고 불필요
            "disclaimer": (intent != "unclear") and not is_prompt,
            "slot_request": slot_request,
            "profile_confirm": profile_confirm,
        }
        return {**state, "assistant_payload": payload}

    async def evidence_extract(self, state: ChatGraphState) -> ChatGraphState:
        evidences = state.get("branch_evidences", [])
        to_save = [
            {
                "chunk_id": e["chunk_id"],
                "snippet": e.get("snippet"),
                "evidence_role": e.get("evidence_role"),
            }
            for e in evidences
            if e.get("chunk_id") is not None
        ]
        return {**state, "evidences_to_save": to_save}

    async def policy_link_extract(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": ""}
        intent: Intent = decision["intent"]
        action_type = _INTENT_TO_ACTION_TYPE.get(intent)
        if action_type is None:
            return {**state, "policy_links_to_save": []}
        links = [
            {"policy_slug": p["slug"], "action_type": action_type}
            for p in state.get("branch_policies", [])
            if p.get("slug")
        ]
        return {**state, "policy_links_to_save": links}

    async def _run_recommendation_lifecycle(
        self,
        user_id: int,
        user_content: str,
        selected_conditions: dict[str, Any] | None = None,
        follow_up_resolved: bool = False,
    ) -> tuple[AiRequestSnapshot | None, str | None]:
        request_id: int | None = None
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_RECOMMEND_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_RECOMMEND_STATEMENT_TIMEOUT}'"
                        )
                    )
                    lifecycle = (
                        self.lifecycle_service or _lifecycle_service_class()()
                    )
                    created = await lifecycle.create_request(
                        db=db,
                        user_id=user_id,
                        request_type="recommendation",
                        source_type=_RECOMMEND_SOURCE_TYPE,
                        raw_query=user_content,
                        selected_conditions=selected_conditions,
                        follow_up_resolved=follow_up_resolved,
                    )
                    request_id = int(created.request_id)
                    await lifecycle.mark_processing(
                        db=db,
                        request_type="recommendation",
                        request_id=request_id,
                    )
                    await db.commit()

                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_RECOMMEND_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_RECOMMEND_STATEMENT_TIMEOUT}'"
                        )
                    )
                    snapshot = await asyncio.wait_for(
                        lifecycle.process_condition_request(
                            db=db,
                            request_type="recommendation",
                            request_id=request_id,
                        ),
                        timeout=_RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS,
                    )
                    await db.commit()
                    return snapshot, None
                except Exception:
                    await db.rollback()
                    raise
        except Exception as exc:
            logger.exception("chat branch_recommend lifecycle failed")
            if request_id is not None:
                await _mark_recommendation_failed(request_id, str(exc))
            return None, "temporary_failure"

    async def _recommend_fallback(
        self,
        state: ChatGraphState,
        message: str,
    ) -> ChatGraphState:
        return {
            **state,
            "branch_content": message,
            "branch_policies": [],
            "branch_evidences": [],
        }

    async def _run_eligibility_branch(
        self,
        *,
        state: ChatGraphState,
        policy_slug: str,
        policy_name: str | None,
        evidences: list[dict],
    ) -> ChatGraphState:
        result_json = await self._run_eligibility_lifecycle(
            user_id=state["user_id"],
            user_content=state["user_content"],
            policy_slug=policy_slug,
        )
        if result_json is None:
            return {
                **state,
                "branch_content": _ELIGIBILITY_FALLBACK_ERROR,
                "branch_user_status": None,
                "branch_policies": [],
                "branch_evidences": evidences,
            }

        content, user_status, policies, result_evidences = _adapt_eligibility_result(
            result_json,
            fallback_slug=policy_slug,
            fallback_policy_name=policy_name,
        )
        result_status = result_json.get("status")
        if result_status == RequestStatus.FOLLOW_UP_REQUIRED.value:
            eligibility_slot_update: dict | None = {
                "slug": policy_slug,
                "eligibility_request_id": result_json.get("request_id"),
                "follow_up_questions": (
                    result_json.get("follow_up_questions")
                    or result_json.get("questions")
                    or []
                ),
                "eligibility_status": RequestStatus.FOLLOW_UP_REQUIRED.value,
            }
        else:
            eligibility_slot_update = {
                "slug": policy_slug,
                "eligibility_request_id": result_json.get("request_id"),
                "follow_up_questions": [],
                "eligibility_status": result_status,
            }
        return {
            **state,
            "branch_content": content,
            "branch_user_status": user_status,
            "branch_policies": policies,
            "branch_evidences": result_evidences or evidences,
            "eligibility_slot_update": eligibility_slot_update,
        }

    async def _run_eligibility_lifecycle(
        self,
        user_id: int,
        user_content: str,
        policy_slug: str,
    ) -> dict[str, Any] | None:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_ELIGIBILITY_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_ELIGIBILITY_STATEMENT_TIMEOUT}'"
                        )
                    )
                    result_json = await asyncio.wait_for(
                        self._eligibility_graph().run(
                            db=db,
                            user_id=user_id,
                            policy_identifier=policy_slug,
                            raw_query=user_content,
                            source_type=_ELIGIBILITY_SOURCE_TYPE,
                        ),
                        timeout=_ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS,
                    )
                    await db.commit()
                    return result_json
                except Exception:
                    await db.rollback()
                    raise
        except Exception:
            logger.exception("chat branch_eligibility lifecycle failed")
            return None

    def _eligibility_graph(self) -> EligibilityGraphRunner:
        if self.eligibility_graph is None:
            from app.ai.graphs.eligibility_graph import EligibilityGraphRunner

            self.eligibility_graph = EligibilityGraphRunner(
                lifecycle_service=self.lifecycle_service
            )
        return self.eligibility_graph

    async def _run_comparison_branch(
        self,
        *,
        state: ChatGraphState,
        slug_a: str,
        slug_b: str,
        evidences: list[dict],
    ) -> ChatGraphState:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    result_json = await self._comparison_graph().run(
                        db,
                        slug_a=slug_a,
                        slug_b=slug_b,
                        user_id=state["user_id"],
                        raw_query=state["user_content"],
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise
        except Exception:
            logger.exception("chat branch_compare comparison graph failed")
            return {
                **state,
                "branch_content": _COMPARE_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": evidences,
            }

        content, policies = _adapt_comparison_result(result_json)
        return {
            **state,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    def _comparison_graph(self) -> ComparisonGraphRunner:
        if self.comparison_graph is None:
            from app.ai.graphs.comparison_graph import ComparisonGraphRunner

            self.comparison_graph = ComparisonGraphRunner()
        return self.comparison_graph

    def _policy_summary_graph(self) -> PolicySummaryGraphRunner:
        if self.policy_summary_graph is None:
            from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner

            self.policy_summary_graph = PolicySummaryGraphRunner()
        return self.policy_summary_graph

    async def _load_policy_detail(self, policy_slug: str) -> dict[str, Any] | None:
        try:
            async with AsyncSessionLocal() as db:
                return await PolicyRepository.find_policy_detail(
                    db,
                    policy_slug=policy_slug,
                )
        except Exception:
            logger.exception("chat policy summary detail lookup failed")
            return None

    async def _run_apply_preparation(
        self,
        user_id: int,
        policy_slug: str,
    ) -> tuple[ApplyPreparationResponse | None, str | None]:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_APPLY_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_APPLY_STATEMENT_TIMEOUT}'"
                        )
                    )
                    response = await asyncio.wait_for(
                        ApplyPreparationService.get(
                            db=db,
                            user_id=user_id,
                            policy_slug=policy_slug,
                        ),
                        timeout=_APPLY_LIFECYCLE_TIMEOUT_SECONDS,
                    )
                    await db.commit()
                    return response, None
                except Exception:
                    await db.rollback()
                    raise
        except AppException as exc:
            if exc.code != ErrorCode.POLICY_NOT_FOUND:
                raise
            logger.info(
                "chat branch_apply preview skipped: %s", exc.message
            )
            return None, "policy_not_found"
        except Exception:
            logger.exception("chat branch_apply preview failed")
            return None, "temporary_failure"

    async def _load_application_period_context(
        self,
        policy_slug: str,
    ) -> dict[str, Any] | None:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text(
                        """
                        SELECT
                            p.application_status,
                            p.application_start_date,
                            p.application_end_date,
                            p.application_end_date AS deadline,
                            pd.application_period_text,
                            cp.source_text,
                            cp.source_fields
                        FROM policy p
                        LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                        LEFT JOIN policy_condition_profile cp
                            ON cp.policy_id = p.policy_id
                        WHERE p.policy_code = :policy_slug
                          AND p.is_active = TRUE
                        """
                    ),
                    {"policy_slug": policy_slug},
                )
                row = result.mappings().one_or_none()
                if row is None:
                    return None
                return {
                    "application_status": row.get("application_status"),
                    "application_start_date": row.get("application_start_date"),
                    "application_end_date": row.get("application_end_date"),
                    "deadline": row.get("deadline"),
                    "application_period_text": row.get("application_period_text"),
                    "source_text": row.get("source_text"),
                    "source_fields": row.get("source_fields"),
                }
        except Exception:
            logger.exception("chat application period context lookup failed")
            return None

    async def _generate_apply_answer(
        self,
        state: ChatGraphState,
        evidences: list[dict],
        apply_card: dict,
        application_period_context: dict[str, Any] | None = None,
    ) -> str:
        system = _BRANCH_SYSTEM_PROMPTS["apply"]
        apply_context = _format_apply_card_context(apply_card)
        if apply_context:
            system = f"{system}\n\n신청 정보:\n{apply_context}"
        period_context = _format_application_period_context(
            application_period_context
        )
        if period_context:
            system = (
                f"{system}\n\n{_APPLICATION_PERIOD_CONTEXT_RULES}"
                f"\n\n신청 기간 내부 참고 정보(사용자 카드에는 표시하지 않음):\n"
                f"{period_context}"
            )
        if evidences:
            rag_context = "\n\n".join(
                f"[{evidence.get('source_title') or '정책'}] {evidence.get('snippet', '')}"
                for evidence in evidences
            )
            system = f"{system}\n\n참고 자료:\n{rag_context}"

        messages: list[BaseMessage] = [SystemMessage(content=system)]
        messages.extend(_history_to_lc_messages(state["history"]))
        messages.append(HumanMessage(content=state["user_content"]))
        try:
            response = await _llm().ainvoke(messages, config=_BRANCH_LLM_CONFIG)
            content = response.content
            return content if isinstance(content, str) else str(content)
        except Exception:
            logger.exception("Apply branch answer generation failed; using fallback")
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."

    async def _branch_with_rag(
        self,
        intent: Intent,
        state: ChatGraphState,
    ) -> ChatGraphState:
        policies, evidences = await self._rag_lookup(state["user_content"])
        content = await self._generate_branch_answer(intent, state, evidences)
        return {
            **state,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    async def _rag_lookup(
        self, query: str
    ) -> tuple[list[dict], list[dict]]:
        try:
            result = await self.rag_service.search(query=query, k=_RAG_TOP_K)
        except Exception:
            logger.exception("RAG lookup failed; returning empty context")
            return [], []

        evidences: list[dict] = []
        seen_chunks: set[int] = set()
        for chunk in result.results:
            if chunk.chunk_id is None or chunk.chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk.chunk_id)
            evidences.append({
                "chunk_id": chunk.chunk_id,
                "snippet": (chunk.chunk_text or "")[:_SNIPPET_LIMIT],
                "source_title": chunk.policy_name,
                "source_url": chunk.source_url,
                "evidence_role": None,
            })
            if len(evidences) >= _EVIDENCES_MAX:
                break

        policies: list[dict] = []
        seen_policies: set[str] = set()
        for chunk in result.results:
            if not chunk.policy_code or chunk.policy_code in seen_policies:
                continue
            seen_policies.add(chunk.policy_code)
            policies.append({
                "policy_id": chunk.policy_code,
                "slug": chunk.policy_code,
                "policy_name": chunk.policy_name or "",
                "summary": None,
                "tag": None,
                "tagTone": None,
            })
            if len(policies) >= _POLICIES_MAX:
                break

        return policies, evidences

    async def _generate_branch_answer(
        self,
        intent: Intent,
        state: ChatGraphState,
        evidences: list[dict],
    ) -> str:
        system = _BRANCH_SYSTEM_PROMPTS[intent]
        if evidences and intent != "unclear":
            context = "\n\n".join(
                f"[{evidence.get('source_title') or '정책'}] {evidence.get('snippet', '')}"
                for evidence in evidences
            )
            system = f"{system}\n\n참고 자료:\n{context}"

        messages: list[BaseMessage] = [SystemMessage(content=system)]
        messages.extend(_history_to_lc_messages(state["history"]))
        messages.append(HumanMessage(content=state["user_content"]))

        try:
            token_callback = _BRANCH_TOKEN_CALLBACK.get()
            if token_callback is not None:
                parts: list[str] = []
                async for chunk in _llm().astream(messages, config=_BRANCH_LLM_CONFIG):
                    content = chunk.content
                    if isinstance(content, str):
                        delta = content
                    elif isinstance(content, list):
                        delta_parts: list[str] = []
                        for item in content:
                            if isinstance(item, str):
                                delta_parts.append(item)
                            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                                delta_parts.append(item["text"])
                        delta = "".join(delta_parts)
                    else:
                        delta = ""
                    if delta:
                        parts.append(delta)
                        await token_callback(delta)
                return "".join(parts)
            response = await _llm().ainvoke(messages, config=_BRANCH_LLM_CONFIG)
            content = response.content
            return content if isinstance(content, str) else str(content)
        except Exception:
            logger.exception("Branch answer generation failed; using fallback")
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."

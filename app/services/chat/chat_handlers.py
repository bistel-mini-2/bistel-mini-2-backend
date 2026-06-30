from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.chat_nodes import (
    _attach_recommendation_context,
    _build_apply_card,
    _detect_assertive_phrases,
    _find_slot_policy_by_slug,
    _format_apply_card_context,
    _format_slot_context,
    _generate_apply_answer,
    _generate_branch_answer,
    _history_to_lc_messages,
    _IntentDecision,
    _is_context_dependent_apply_question,
    _is_context_dependent_compare_question,
    _lifecycle_service_class,
    _llm,
    _load_application_period_context,
    _load_db_profile_summary,
    _load_policy_detail,
    _mark_recommendation_failed,
    _pick_apply_target,
    _pick_compare_targets,
    _recent_assistant_policy_target,
    _recommend_follow_up_already_asked,
    _run_apply_preparation,
    _summary_mode,
    _summary_target_id,
    _summary_target_type,
    _user_mentions_policy_name,
)
from app.ai.nodes.chat.profile_helpers import (
    _build_slot_question,
    _filled_slots,
    _format_profile_context,
    _interpret_confirm,
    _merge_profile,
    _missing_required,
    _profile_to_selected_conditions,
)
from app.ai.nodes.chat.result_adapters import (
    _adapt_comparison_result,
    _adapt_eligibility_result,
    _adapt_policy_summary_result,
    _adapt_recommendation_result,
    _policy_summary_fallback_content,
)
from app.ai.nodes.chat.constants import (
    APPLY_CLARIFICATION_FALLBACK as _APPLY_CLARIFICATION_FALLBACK,
    APPLY_MAX_RETRIES as _APPLY_MAX_RETRIES,
    APPLY_TEMPORARY_FAILURE_FALLBACK as _APPLY_TEMPORARY_FAILURE_FALLBACK,
    COMPARE_CLARIFICATION_FALLBACK as _COMPARE_CLARIFICATION_FALLBACK,
    COMPARE_FALLBACK_ERROR as _COMPARE_FALLBACK_ERROR,
    ELIGIBILITY_CLARIFICATION_FALLBACK as _ELIGIBILITY_CLARIFICATION_FALLBACK,
    ELIGIBILITY_FALLBACK_ERROR as _ELIGIBILITY_FALLBACK_ERROR,
    ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS as _ELIGIBILITY_LIFECYCLE_TIMEOUT_SECONDS,
    ELIGIBILITY_LOCK_TIMEOUT as _ELIGIBILITY_LOCK_TIMEOUT,
    ELIGIBILITY_SOURCE_TYPE as _ELIGIBILITY_SOURCE_TYPE,
    ELIGIBILITY_STATEMENT_TIMEOUT as _ELIGIBILITY_STATEMENT_TIMEOUT,
    EVIDENCES_MAX as _EVIDENCES_MAX,
    INTENT_TO_ACTION_TYPE as _INTENT_TO_ACTION_TYPE,
    INTENT_TO_API_ACTION as _INTENT_TO_API_ACTION,
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
from app.ai.nodes.chat.prompts import SUPERVISOR_SYSTEM_TEMPLATE
from app.ai.nodes.chat.slots import (
    CONFIRM_OPTIONS,
    RECOMMEND_WIZARD_FIELDS,
    REQUIRED_SLOTS,
    SLOT_LABELS as _SLOT_LABELS,
    SLOT_OPTIONS as _SLOT_OPTIONS,
    SLOT_QUESTIONS as _SLOT_QUESTIONS,
)
from app.ai.states.chat_state import (
    ChatGraphState,
    ChatSlot,
    HistoryMessage,
    Intent,
    PendingState,
    ProfileSlot,
)
from app.common.ai_status import RequestStatus
from app.db.session import AsyncSessionLocal
from app.schemas.ai_request_schema import AiRequestSnapshot

if TYPE_CHECKING:
    from app.ai.graphs.comparison_graph import ComparisonGraphRunner
    from app.ai.graphs.eligibility_graph import EligibilityGraphRunner
    from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService
    from app.services.policy_rag_service import PolicyRagService


logger = logging.getLogger(__name__)

import re as _re
# 조건 트리 / 매칭 결과 raw 데이터 패턴 감지 (evidence 필터링용)
_RAW_STRUCTURED_RE = _re.compile(
    r"\b(?:operator|matchingstrength|confidence):\s*\S"
    r"|\bsourcetext:\s*\S.*\breason:\s*[A-Z_]{5}",
    _re.IGNORECASE | _re.DOTALL,
)


def _is_raw_structured_snippet(text: str) -> bool:
    return bool(_RAW_STRUCTURED_RE.search(text))


_POLICY_SUMMARY_CLARIFICATION_FALLBACK = (
    "어떤 정책을 요약할까요? 정책명을 알려주시면 핵심 위주로 쉽게 정리해 드릴게요."
)

# ─── lazy singletons ────────────────────────────────────────────────────────

_RAG_SERVICE: PolicyRagService | None = None
_LIFECYCLE_SERVICE: AiRequestLifecycleService | None = None
_ELIGIBILITY_GRAPH: EligibilityGraphRunner | None = None
_COMPARISON_GRAPH: ComparisonGraphRunner | None = None
_POLICY_SUMMARY_GRAPH: PolicySummaryGraphRunner | None = None


def _rag_service() -> PolicyRagService:
    global _RAG_SERVICE
    if _RAG_SERVICE is None:
        from app.services.policy_rag_service import PolicyRagService
        _RAG_SERVICE = PolicyRagService()
    return _RAG_SERVICE


def _get_eligibility_graph() -> EligibilityGraphRunner:
    global _ELIGIBILITY_GRAPH
    if _ELIGIBILITY_GRAPH is None:
        from app.ai.graphs.eligibility_graph import EligibilityGraphRunner
        _ELIGIBILITY_GRAPH = EligibilityGraphRunner()
    return _ELIGIBILITY_GRAPH


def _get_comparison_graph() -> ComparisonGraphRunner:
    global _COMPARISON_GRAPH
    if _COMPARISON_GRAPH is None:
        from app.ai.graphs.comparison_graph import ComparisonGraphRunner
        _COMPARISON_GRAPH = ComparisonGraphRunner()
    return _COMPARISON_GRAPH


def _get_policy_summary_graph() -> PolicySummaryGraphRunner:
    global _POLICY_SUMMARY_GRAPH
    if _POLICY_SUMMARY_GRAPH is None:
        from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
        _POLICY_SUMMARY_GRAPH = PolicySummaryGraphRunner()
    return _POLICY_SUMMARY_GRAPH


# ─── module-level helpers ───────────────────────────────────────────────────


async def _rag_lookup(query: str) -> tuple[list[dict], list[dict]]:
    try:
        result = await _rag_service().search(query=query, k=_RAG_TOP_K)
    except Exception:
        logger.exception("RAG lookup failed; returning empty context")
        return [], []

    evidences: list[dict] = []
    seen_chunks: set[int] = set()
    for chunk in result.results:
        if chunk.chunk_id is None or chunk.chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk.chunk_id)
        snippet = (chunk.chunk_text or "")[:_SNIPPET_LIMIT]
        if _is_raw_structured_snippet(snippet):
            continue
        evidences.append({
            "chunk_id": chunk.chunk_id,
            "snippet": snippet,
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


async def _branch_with_rag(intent: Intent, state: ChatGraphState) -> ChatGraphState:
    policies, evidences = await _rag_lookup(state["user_content"])
    content = await _generate_branch_answer(intent, state, evidences)
    return {
        **state,
        "branch_content": content,
        "branch_policies": policies,
        "branch_evidences": evidences,
    }


def _recent_slot_policies(slot: ChatSlot | None) -> list[dict[str, Any]]:
    return list((slot or {}).get("recent_policies") or [])


def _single_recent_policy_target(
    state: ChatGraphState,
) -> tuple[str | None, str | None]:
    recent = _recent_slot_policies(state.get("slot"))
    if len(recent) == 1 and recent[0].get("slug"):
        return str(recent[0]["slug"]), recent[0].get("policy_name") or None

    return None, None


def _is_contextual_single_policy_request(intent: Intent, user_content: str) -> bool:
    if intent in ("summary", "policy_summary"):
        return any(token in user_content for token in ("요약", "정리", "뭐야", "무엇"))
    if intent == "eligibility":
        return any(
            token in user_content
            for token in ("지원 가능", "지원가능", "받을 수", "대상", "자격", "조건")
        )
    if intent == "apply":
        return _is_context_dependent_apply_question(user_content)
    return False


def _mentioned_recent_policy_targets(
    state: ChatGraphState,
) -> list[tuple[str, str | None]]:
    targets: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for policy in _recent_slot_policies(state.get("slot")):
        slug = policy.get("slug")
        if not slug or slug in seen:
            continue
        if _user_mentions_policy_name(state["user_content"], policy.get("policy_name")):
            seen.add(str(slug))
            targets.append((str(slug), policy.get("policy_name") or None))
    return targets


_COMPARE_QUERY_GENERIC_TOKENS = {
    "비교",
    "비교해",
    "비교해줘",
    "알려줘",
    "추천",
    "정책",
    "비슷한",
}


def _extract_compare_policy_name_parts(user_content: str) -> list[str]:
    content = re.sub(
        r"(비교해\s*줘|비교해줘|비교해|비교|차이(?:점)?|알려\s*줘|알려줘)",
        " ",
        user_content,
    )
    parts = [
        re.sub(r"(?:을|를|은|는|이|가)$", "", part.strip(" \t\r\n'\"“”‘’.,!?？"))
        for part in re.split(r"\s*(?:와|과|이랑|랑|하고|및|,|/)\s*", content)
    ]
    return [
        part
        for part in parts
        if len(re.sub(r"[^0-9A-Za-z가-힣]+", "", part)) >= 2
        and part not in _COMPARE_QUERY_GENERIC_TOKENS
    ]


def _policy_name_match_score(query_part: str, policy_name: str | None) -> int:
    if not query_part or not policy_name:
        return 0
    normalized_query = re.sub(r"[^0-9A-Za-z가-힣]+", "", query_part).lower()
    normalized_name = re.sub(r"[^0-9A-Za-z가-힣]+", "", policy_name).lower()
    if not normalized_query or not normalized_name:
        return 0
    if _user_mentions_policy_name(query_part, policy_name):
        return 1000 + min(len(normalized_query), len(normalized_name))
    if normalized_query in normalized_name:
        return 700 + len(normalized_query)

    tokens = [
        re.sub(r"[^0-9A-Za-z가-힣]+", "", token).lower()
        for token in re.findall(r"[0-9A-Za-z가-힣]+", query_part)
    ]
    tokens = [
        token
        for token in tokens
        if len(token) >= 2 and token not in _COMPARE_QUERY_GENERIC_TOKENS
    ]
    matched_tokens = [token for token in tokens if token in normalized_name]
    if not matched_tokens:
        return 0
    return sum(len(token) for token in matched_tokens)


async def _find_compare_targets_by_policy_names(
    user_content: str,
) -> tuple[tuple[str, str | None] | None, tuple[str, str | None] | None]:
    parts = _extract_compare_policy_name_parts(user_content)
    if len(parts) < 2:
        return None, None

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text(
                """
                SELECT policy_code AS slug, policy_name
                FROM policy
                WHERE is_active = TRUE
                  AND policy_code IS NOT NULL
                  AND policy_name IS NOT NULL
                """
            )
        )
        policies = [dict(row) for row in result.mappings().all()]

    targets: list[tuple[str, str | None]] = []
    used_slugs: set[str] = set()
    for part in parts[:3]:
        scored = sorted(
            (
                (
                    _policy_name_match_score(part, str(policy.get("policy_name") or "")),
                    policy,
                )
                for policy in policies
                if str(policy.get("slug") or "") not in used_slugs
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scored or scored[0][0] <= 0:
            continue
        policy = scored[0][1]
        slug = str(policy.get("slug") or "")
        if not slug:
            continue
        used_slugs.add(slug)
        targets.append((slug, str(policy.get("policy_name") or "") or None))
        if len(targets) >= 2:
            break

    if len(targets) < 2:
        return None, None
    return targets[0], targets[1]


async def _resolve_single_policy_target(
    state: ChatGraphState,
    *,
    intent: Intent,
) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    decision = state.get("supervisor_decision") or {}
    resolved_slug = decision.get("resolved_policy_slug")
    if resolved_slug:
        slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug)
        logger.info(
            "chat_slot_resolved",
            extra={"intent": intent, "slot_used": bool(slot_policy), "rag_skipped": True},
        )
        return str(resolved_slug), (slot_policy or {}).get("policy_name"), []

    mentioned_recent = _mentioned_recent_policy_targets(state)
    if len(mentioned_recent) == 1:
        logger.info(
            "chat_slot_resolved",
            extra={"intent": intent, "slot_used": True, "rag_skipped": True},
        )
        return mentioned_recent[0][0], mentioned_recent[0][1], []

    is_clarification_resume = (
        (state.get("slot") or {}).get("pending", {}) or {}
    ).get("kind") == "clarification"

    policies, evidences = await _rag_lookup(state["user_content"])
    slug, policy_name = _pick_apply_target(
        policies,
        user_content=state["user_content"],
        require_policy_name_mention=True,
    )
    if slug is None and is_clarification_resume and policies:
        slug, policy_name = _pick_apply_target(policies, require_policy_name_mention=False)
    logger.info(
        "chat_slot_resolved",
        extra={
            "intent": intent,
            "slot_used": False,
            "rag_skipped": False,
            "explicit_policy_found": slug is not None,
            "clarification_resume": is_clarification_resume,
        },
    )
    if slug is not None:
        return slug, policy_name, evidences

    slug, policy_name = _single_recent_policy_target(state)
    if slug is not None:
        logger.info(
            "chat_slot_resolved",
            extra={"intent": intent, "slot_used": True, "rag_skipped": False},
        )
        return slug, policy_name, []

    if (
        not _recent_slot_policies(state.get("slot"))
        and _is_contextual_single_policy_request(intent, state["user_content"])
    ):
        slug, policy_name = _recent_assistant_policy_target(
            state.get("recent_assistant_policy")
        )
        if slug is not None:
            logger.info(
                "chat_recent_assistant_policy_resolved",
                extra={"intent": intent, "recent_policy_used": True},
            )
            return slug, policy_name, []

    return None, None, []


async def _resolve_compare_targets(
    state: ChatGraphState,
) -> tuple[
    tuple[str, str | None] | None,
    tuple[str, str | None] | None,
    list[dict[str, Any]],
]:
    mentioned_recent = _mentioned_recent_policy_targets(state)
    if len(mentioned_recent) == 2:
        return mentioned_recent[0], mentioned_recent[1], []
    if len(mentioned_recent) == 1:
        policies, evidences = await _rag_lookup(state["user_content"])
        explicit_targets = [
            target
            for target in _pick_compare_targets(
                policies,
                slot=None,
                user_content=state["user_content"],
            )
            if target is not None
        ]
        combined = [mentioned_recent[0]]
        seen = {mentioned_recent[0][0]}
        for target in explicit_targets:
            if target[0] not in seen:
                combined.append(target)
                seen.add(target[0])
        if len(combined) == 2:
            return combined[0], combined[1], evidences
        return None, None, []

    recent = _recent_slot_policies(state.get("slot"))
    if len(recent) == 2 and _is_context_dependent_compare_question(state["user_content"]):
        first = (str(recent[0]["slug"]), recent[0].get("policy_name") or None)
        second = (str(recent[1]["slug"]), recent[1].get("policy_name") or None)
        return first, second, []

    policies, evidences = await _rag_lookup(state["user_content"])
    first, second = _pick_compare_targets(
        policies,
        slot=None,
        user_content=state["user_content"],
    )
    if first is None or second is None:
        direct_first, direct_second = await _find_compare_targets_by_policy_names(
            state["user_content"]
        )
        if direct_first is not None and direct_second is not None:
            return direct_first, direct_second, evidences
        return None, None, []
    return first, second, evidences


async def _run_recommendation_lifecycle(
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
                    _LIFECYCLE_SERVICE if _LIFECYCLE_SERVICE is not None
                    else _lifecycle_service_class()()
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


async def _run_eligibility_lifecycle(
    user_id: int,
    user_content: str,
    policy_slug: str,
    selected_conditions: dict[str, Any] | None = None,
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
                    _get_eligibility_graph().run(
                        db=db,
                        user_id=user_id,
                        policy_identifier=policy_slug,
                        raw_query=user_content,
                        source_type=_ELIGIBILITY_SOURCE_TYPE,
                        selected_conditions=selected_conditions,
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


async def _run_eligibility_branch(
    *,
    state: ChatGraphState,
    policy_slug: str,
    policy_name: str | None,
    evidences: list[dict],
) -> ChatGraphState:
    result_json = await _run_eligibility_lifecycle(
        user_id=state["user_id"],
        user_content=state["user_content"],
        policy_slug=policy_slug,
        selected_conditions=_profile_to_selected_conditions(state.get("profile")) or None,
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
        "branch_eligibility_result": {
            "status": result_status,
            "user_status": result_json.get("user_status"),
            "assessment_status": result_json.get("assessment_status"),
            "follow_up_questions": result_json.get("follow_up_questions") or [],
            "summary": result_json.get("summary"),
            "request_id": result_json.get("request_id"),
            "criteria": result_json.get("criteria") or result_json.get("criteria_results") or [],
        },
    }


async def _run_comparison_branch(
    *,
    state: ChatGraphState,
    slug_a: str,
    slug_b: str,
    evidences: list[dict],
) -> ChatGraphState:
    try:
        async with AsyncSessionLocal() as db:
            try:
                result_json = await _get_comparison_graph().run(
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


# ─── intent classification ──────────────────────────────────────────────────


async def classify_intent(
    *,
    user_content: str,
    history: list[HistoryMessage],
    slot: ChatSlot | dict | None,
    recent_assistant_policy: dict[str, Any] | None,
    user_id: int,
) -> ChatGraphState:
    """Classify intent and prepare slot/profile routing state."""
    state: ChatGraphState = {
        "user_id": user_id,
        "user_content": user_content,
        "history": history,
        "slot": slot or {},
        "recent_assistant_policy": recent_assistant_policy,
    }

    llm = _llm().with_structured_output(_IntentDecision)
    slot_state = state.get("slot")
    profile: ProfileSlot = (slot_state or {}).get("profile") or {}
    pending: PendingState | None = (slot_state or {}).get("pending") or None
    slot_context = _format_slot_context(slot_state)
    profile_context = _format_profile_context(profile)
    system_prompt = SUPERVISOR_SYSTEM_TEMPLATE.format(
        slot_context=slot_context, profile_context=profile_context
    )
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    messages.extend(_history_to_lc_messages(state["history"]))
    messages.append(HumanMessage(content=state["user_content"]))

    resolved_slug: str | None = None
    extracted: dict[str, Any] | None = None
    raw = "{}"
    try:
        decision = await llm.ainvoke(messages)
        intent: Intent = decision.intent
        raw = decision.model_dump_json()
        candidate = decision.resolved_policy_slug
        if candidate and _find_slot_policy_by_slug(slot_state, candidate):
            resolved_slug = candidate
        if decision.extracted_profile is not None:
            extracted = decision.extracted_profile.model_dump(exclude_none=True)
    except Exception as exc:
        logger.exception("Intent classification failed; falling back to unclear")
        intent = "unclear"
        raw = f"error: {exc}"

    # 1) 추출한 조건을 세션 프로필에 병합 (모든 intent 공유)
    profile = _merge_profile(profile, extracted)

    prev_kind = (pending or {}).get("kind", "slot") if pending else None
    profile_confirm: dict[str, Any] | None = None
    awaiting: list[str] = []

    # 2-A) 직전 턴이 "회원 프로필 확인" 프롬프트였으면 yes/no 로 처리
    if pending and prev_kind == "confirm" and intent in ("unclear", "recommend"):
        intent = "recommend"
        answer = _interpret_confirm(state["user_content"])
        if answer == "yes":
            awaiting = []
            profile = {**profile, "db_profile_confirmed": True}
        elif answer == "no":
            awaiting = list(REQUIRED_SLOTS.get("recommend", ()))
        else:
            awaiting = _missing_required("recommend", profile)
        logger.info("chat_profile_confirm_resume", extra={"answer": answer})
    elif pending and prev_kind == "clarification" and pending.get("intent"):
        # 2-B) 직전 턴이 "정책 명확화 요청"이었으면 LLM 분류 무시하고 강제 이어받기.
        # 사용자가 어떤 말을 해도 이전 intent 핸들러가 다시 정책을 찾도록 한다.
        # 핸들러가 정책을 찾으면 pending=None 반환 → 상태 클리어.
        # 못 찾으면 다시 clarification pending 저장 → 재질문.
        intent = pending["intent"]
        logger.info("chat_clarification_resume", extra={"pending_intent": intent})
    else:
        # 2-C) 채우던 슬롯이 있으면 이어받기 판단
        resumed = False
        if pending and pending.get("intent") and prev_kind != "confirm":
            pending_intent = pending["intent"]
            pending_awaiting = pending.get("awaiting") or list(
                REQUIRED_SLOTS.get(pending_intent, ())
            )
            answered_slot = bool(extracted) and any(
                key in (extracted or {}) for key in pending_awaiting
            )
            if intent in ("unclear", pending_intent) or answered_slot:
                intent = pending_intent
                resumed = True
                unfilled = _missing_required(intent, profile)
                if unfilled:
                    asked = pending.get("awaiting") or unfilled
                    skipped = set(profile.get("skipped") or [])
                    skipped.update(s for s in unfilled if s in asked)
                    profile = {**profile, "skipped": sorted(skipped)}
            logger.info(
                "chat_pending_resume",
                extra={"pending_intent": pending_intent, "resumed": resumed},
            )

        # 3) 결정된 intent의 필수 슬롯 중 빠진 것 계산 (건너뛴 슬롯 제외)
        awaiting = _missing_required(intent, profile)
        # 3-A) 추천인데 세션 조건이 부족하면 저장된 회원 프로필 확인부터.
        if intent == "recommend" and awaiting:
            summary = await _load_db_profile_summary(state["user_id"])
            if summary:
                profile_confirm = {"summary": summary, "options": CONFIRM_OPTIONS}
                awaiting = []

    pending_active = bool(awaiting or profile_confirm)
    return {
        **state,
        "profile": profile,
        "pending_intent": intent if pending_active else None,
        "awaiting_slots": awaiting,
        "profile_confirm": profile_confirm,
        "supervisor_decision": {
            "intent": intent,
            "raw": raw,
            "resolved_policy_slug": resolved_slug,
        },
    }


# ─── branch handlers ────────────────────────────────────────────────────────


async def handle_recommend(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    del db
    selected_conditions = _profile_to_selected_conditions(state.get("profile"))
    follow_up_already_asked = _recommend_follow_up_already_asked(
        state.get("slot")
    ) or bool((state.get("profile") or {}).get("db_profile_confirmed"))

    async def _branch(s: ChatGraphState) -> ChatGraphState:
        snapshot, lifecycle_error = await _run_recommendation_lifecycle(
            user_id=s["user_id"],
            user_content=s["user_content"],
            selected_conditions=selected_conditions or None,
            follow_up_resolved=follow_up_already_asked,
        )
        if lifecycle_error == "temporary_failure":
            return {
                **s,
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
                **s,
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
                    **s,
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
                    **s,
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
                **s,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_content": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot.status != RequestStatus.COMPLETED:
            return {
                **s,
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
        content = await _generate_branch_answer("recommend", s, evidences)
        return {
            **s,
            "recommend_flow_status": "ok",
            "recommend_error_message": None,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    current: ChatGraphState = await _branch(state)

    while current.get("recommend_flow_status") == "retryable_error":
        retry_count = int(current.get("recommend_retry_count") or 0) + 1
        current = {**current, "recommend_retry_count": retry_count}
        max_retries = int(current.get("recommend_max_retries") or 1)
        if retry_count <= max_retries:
            current = await _branch(current)
        else:
            fallback_message = current.get("recommend_error_message") or _RECOMMEND_FALLBACK_ERROR
            current = {
                **current,
                "recommend_flow_status": "ok",
                "branch_content": fallback_message,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
            break

    if current.get("recommend_flow_status") == "fallback_needed":
        fallback_message = current.get("recommend_error_message") or _RECOMMEND_FALLBACK_ERROR
        current = {
            **current,
            "recommend_flow_status": "ok",
            "branch_content": fallback_message,
            "branch_policies": [],
            "branch_evidences": [],
            "branch_apply_card": None,
        }

    return current


async def handle_eligibility(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    del db
    slug, policy_name, evidences = await _resolve_single_policy_target(
        state,
        intent="eligibility",
    )
    if slug is None:
        return {
            **state,
            "branch_content": _ELIGIBILITY_CLARIFICATION_FALLBACK,
            "branch_user_status": None,
            "branch_policies": [],
            "branch_evidences": evidences,
        }
    return await _run_eligibility_branch(
        state=state,
        policy_slug=slug,
        policy_name=policy_name,
        evidences=evidences,
    )


async def handle_compare(
    state: ChatGraphState,
) -> dict[str, Any]:
    first, second, evidences = await _resolve_compare_targets(state)
    if first is None or second is None:
        return {
            **state,
            "branch_content": _COMPARE_CLARIFICATION_FALLBACK,
            "branch_policies": [],
            "branch_evidences": evidences,
        }
    return await _run_comparison_branch(
        state=state,
        slug_a=first[0],
        slug_b=second[0],
        evidences=evidences,
    )


async def handle_apply(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    del db
    slug, policy_name, evidences = await _resolve_single_policy_target(
        state,
        intent="apply",
    )

    async def _branch(s: ChatGraphState) -> ChatGraphState:
        if slug is None:
            return {
                **s,
                "apply_flow_status": "fallback_needed",
                "apply_error_message": _APPLY_CLARIFICATION_FALLBACK,
                "branch_content": _APPLY_CLARIFICATION_FALLBACK,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }

        apply_response, apply_error = await _run_apply_preparation(
            user_id=s["user_id"],
            policy_slug=slug,
        )
        if apply_error == "temporary_failure":
            return {
                **s,
                "apply_flow_status": "retryable_error",
                "apply_error_message": _APPLY_TEMPORARY_FAILURE_FALLBACK,
                "apply_max_retries": _APPLY_MAX_RETRIES,
                "branch_content": _APPLY_TEMPORARY_FAILURE_FALLBACK,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        if apply_response is None:
            content = await _generate_branch_answer("apply", s, evidences)
            return {
                **s,
                "apply_flow_status": "ok",
                "apply_error_message": None,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        apply_card = _build_apply_card(apply_response, policy_name)
        application_period_context = await _load_application_period_context(slug)
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
        content = await _generate_apply_answer(
            s,
            evidences,
            apply_card,
            application_period_context,
        )
        return {
            **s,
            "apply_flow_status": "ok",
            "apply_error_message": None,
            "branch_content": content,
            "branch_policies": apply_policies,
            "branch_evidences": evidences,
            "branch_apply_card": apply_card,
        }

    current: ChatGraphState = await _branch(state)

    while current.get("apply_flow_status") == "retryable_error":
        retry_count = int(current.get("apply_retry_count") or 0) + 1
        current = {**current, "apply_retry_count": retry_count}
        max_retries = int(current.get("apply_max_retries") or 1)
        if retry_count <= max_retries:
            current = await _branch(current)
        else:
            fallback_message = (
                current.get("apply_error_message")
                or "신청 안내를 준비하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
            )
            current = {
                **current,
                "apply_flow_status": "ok",
                "branch_content": fallback_message,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
            break

    if current.get("apply_flow_status") == "fallback_needed":
        fallback_message = (
            current.get("apply_error_message")
            or "신청 안내를 준비하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
        )
        current = {
            **current,
            "apply_flow_status": "ok",
            "branch_content": fallback_message,
            "branch_policies": [],
            "branch_evidences": [],
            "branch_apply_card": None,
        }

    return current


async def _run_policy_summary_target(
    state: ChatGraphState,
    *,
    policy_slug: str,
    policy_name: str | None,
    fallback_evidences: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = await _load_policy_detail(policy_slug)
    if policy is None:
        return {
            **state,
            "branch_content": "해당 정책 정보를 찾지 못했어요. 정책명을 다시 확인해 주세요.",
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
        summary_result = await _get_policy_summary_graph().run(policy)
    except Exception:
        logger.exception("Policy summary graph failed")
        return {
            **state,
            "branch_content": "정책 요약을 만드는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
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


async def handle_policy_summary(
    state: ChatGraphState,
) -> dict[str, Any]:
    policy_slug, policy_name, fallback_evidences = await _resolve_single_policy_target(
        state,
        intent="policy_summary",
    )
    if not policy_slug:
        return {
            **state,
            "branch_content": _POLICY_SUMMARY_CLARIFICATION_FALLBACK,
            "branch_policies": [],
            "branch_evidences": [],
            "pending": {"intent": "policy_summary", "kind": "clarification"},
        }
    return await _run_policy_summary_target(
        state,
        policy_slug=policy_slug,
        policy_name=policy_name,
        fallback_evidences=fallback_evidences,
    )


async def handle_summary(
    state: ChatGraphState,
) -> dict[str, Any]:
    policy_slug, policy_name, fallback_evidences = await _resolve_single_policy_target(
        state,
        intent="summary",
    )
    if not policy_slug:
        return {
            **state,
            "branch_content": _POLICY_SUMMARY_CLARIFICATION_FALLBACK,
            "branch_policies": [],
            "branch_evidences": [],
            "pending": {"intent": "summary", "kind": "clarification"},
        }
    return await _run_policy_summary_target(
        state,
        policy_slug=policy_slug,
        policy_name=policy_name,
        fallback_evidences=fallback_evidences,
    )


async def handle_unclear(
    state: ChatGraphState,
) -> dict[str, Any]:
    content = await _generate_branch_answer("unclear", state, evidences=[])
    return {
        **state,
        "branch_content": content,
        "branch_policies": [],
        "branch_evidences": [],
    }


async def handle_collect_slots(
    state: ChatGraphState,
) -> dict[str, Any]:
    awaiting = state.get("awaiting_slots") or []
    profile: ProfileSlot = state.get("profile") or {}
    decision = state.get("supervisor_decision") or {}
    intent: Intent = decision.get("intent", "recommend")

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
        "multi": ["special"],
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


async def handle_confirm_profile(
    state: ChatGraphState,
) -> dict[str, Any]:
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


async def build_assistant_payload(
    state: ChatGraphState,
) -> dict[str, Any]:
    decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": "missing"}
    intent: Intent = decision["intent"]
    slot_request = state.get("slot_request")
    profile_confirm = state.get("profile_confirm")
    is_prompt = bool(slot_request or profile_confirm)
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
        "disclaimer": False,
        "slot_request": slot_request,
        "profile_confirm": profile_confirm,
        "eligibility_result": state.get("branch_eligibility_result"),
    }
    return {"assistant_payload": payload}


async def extract_evidences(
    state: ChatGraphState,
) -> list[dict[str, Any]]:
    evidences = state.get("branch_evidences", [])
    return [
        {
            "chunk_id": e["chunk_id"],
            "snippet": e.get("snippet"),
            "evidence_role": e.get("evidence_role"),
        }
        for e in evidences
        if e.get("chunk_id") is not None
    ]


async def extract_policy_links(
    state: ChatGraphState,
) -> list[dict[str, Any]]:
    decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": ""}
    intent: Intent = decision["intent"]
    action_type = _INTENT_TO_ACTION_TYPE.get(intent)
    if action_type is None:
        return []
    return [
        {"policy_slug": p["slug"], "action_type": action_type}
        for p in state.get("branch_policies", [])
        if p.get("slug")
    ]


__all__ = [
    "build_assistant_payload",
    "classify_intent",
    "extract_evidences",
    "extract_policy_links",
    "handle_apply",
    "handle_collect_slots",
    "handle_compare",
    "handle_confirm_profile",
    "handle_eligibility",
    "handle_policy_summary",
    "handle_recommend",
    "handle_summary",
    "handle_unclear",
]

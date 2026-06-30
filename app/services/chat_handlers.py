from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.chat_nodes import (
    ChatGraphNodes,
    _find_slot_policy_by_slug,
    _format_profile_context,
    _format_slot_context,
    _history_to_lc_messages,
    _IntentDecision,
    _interpret_confirm,
    _llm,
    _load_db_profile_summary,
    _merge_profile,
    _missing_required,
    _summary_target_type,
)
from app.ai.nodes.chat.prompts import SUPERVISOR_SYSTEM_TEMPLATE
from app.ai.nodes.chat.slots import CONFIRM_OPTIONS, REQUIRED_SLOTS
from app.ai.states.chat_state import (
    ChatGraphState,
    ChatSlot,
    HistoryMessage,
    Intent,
    PendingState,
    ProfileSlot,
)


logger = logging.getLogger(__name__)


_DEFAULT_NODES: ChatGraphNodes | None = None


def _nodes(nodes: ChatGraphNodes | None = None) -> ChatGraphNodes:
    global _DEFAULT_NODES
    if nodes is not None:
        return nodes
    if _DEFAULT_NODES is None:
        _DEFAULT_NODES = ChatGraphNodes()
    return _DEFAULT_NODES


async def classify_intent(
    *,
    user_content: str,
    history: list[HistoryMessage],
    slot: ChatSlot | dict | None,
    recent_assistant_policy: dict[str, Any] | None,
    user_id: int,
    nodes: ChatGraphNodes | None = None,
) -> ChatGraphState:
    """Classify intent and prepare slot/profile routing state."""
    del nodes
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
    else:
        # 2-B) 채우던 슬롯이 있으면 이어받기 판단
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
        if (
            intent == "summary"
            and _summary_target_type(
                state["user_content"],
                slot_state,
                resolved_slug,
            )
            is None
        ):
            awaiting = ["summary_target"]

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


async def handle_recommend(
    state: ChatGraphState,
    db: AsyncSession | None = None,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former recommend branch, including graph-level retry/fallback."""
    del db
    handler_nodes = _nodes(nodes)
    current: ChatGraphState = await handler_nodes.branch_recommend(state)

    while current.get("recommend_flow_status") == "retryable_error":
        current = await handler_nodes.recommend_retry_increment(current)
        retry_count = int(current.get("recommend_retry_count") or 0)
        max_retries = int(current.get("recommend_max_retries") or 1)
        if retry_count <= max_retries:
            current = await handler_nodes.branch_recommend(current)
        else:
            current = await handler_nodes.recommend_fallback_build(current)
            break

    if current.get("recommend_flow_status") == "fallback_needed":
        current = await handler_nodes.recommend_fallback_build(current)

    return current


async def handle_eligibility(
    state: ChatGraphState,
    db: AsyncSession | None = None,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former eligibility branch."""
    del db
    return await _nodes(nodes).branch_eligibility(state)


async def handle_compare(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former compare branch."""
    return await _nodes(nodes).branch_compare(state)


async def handle_apply(
    state: ChatGraphState,
    db: AsyncSession | None = None,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former apply branch, including graph-level retry/fallback."""
    del db
    handler_nodes = _nodes(nodes)
    current: ChatGraphState = await handler_nodes.branch_apply(state)

    while current.get("apply_flow_status") == "retryable_error":
        current = await handler_nodes.apply_retry_increment(current)
        retry_count = int(current.get("apply_retry_count") or 0)
        max_retries = int(current.get("apply_max_retries") or 1)
        if retry_count <= max_retries:
            current = await handler_nodes.branch_apply(current)
        else:
            current = await handler_nodes.apply_fallback_build(current)
            break

    if current.get("apply_flow_status") == "fallback_needed":
        current = await handler_nodes.apply_fallback_build(current)

    return current


async def handle_policy_summary(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former policy summary branch."""
    return await _nodes(nodes).branch_policy_summary(state)


async def handle_summary(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former summary branch."""
    return await _nodes(nodes).branch_summary(state)


async def handle_unclear(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former unclear branch."""
    return await _nodes(nodes).branch_unclear(state)


async def handle_collect_slots(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former collect slots branch."""
    return await _nodes(nodes).collect_slots(state)


async def handle_confirm_profile(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Run the former profile confirmation branch."""
    return await _nodes(nodes).confirm_profile(state)


async def build_assistant_payload(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> dict[str, Any]:
    """Build the same assistant payload produced by the former graph tail."""
    result = await _nodes(nodes).assistant_payload_build(state)
    return {"assistant_payload": result.get("assistant_payload")}


async def extract_evidences(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> list[dict[str, Any]]:
    """Extract evidence rows from the handler state."""
    result = await _nodes(nodes).evidence_extract(state)
    return result.get("evidences_to_save", [])


async def extract_policy_links(
    state: ChatGraphState,
    *,
    nodes: ChatGraphNodes | None = None,
) -> list[dict[str, Any]]:
    """Extract policy link rows from the handler state."""
    result = await _nodes(nodes).policy_link_extract(state)
    return result.get("policy_links_to_save", [])


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

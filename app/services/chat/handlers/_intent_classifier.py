from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.ai.nodes.chat.chat_nodes import (
    _find_slot_policy_by_slug,
    _format_slot_context,
    _history_to_lc_messages,
    _IntentDecision,
    _llm,
    _load_db_profile_summary,
)
from app.ai.nodes.chat.profile_helpers import (
    _format_profile_context,
    _interpret_confirm,
    _merge_profile,
    _missing_required,
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

    profile = _merge_profile(profile, extracted)

    prev_kind = (pending or {}).get("kind", "slot") if pending else None
    profile_confirm: dict[str, Any] | None = None
    awaiting: list[str] = []

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
        intent = pending["intent"]
        logger.info("chat_clarification_resume", extra={"pending_intent": intent})
    else:
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

        awaiting = _missing_required(intent, profile)
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

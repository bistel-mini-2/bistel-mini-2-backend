from __future__ import annotations

import logging
from typing import Any

from app.ai.nodes.chat.chat_nodes import (
    _summary_mode,
    _summary_target_id,
    _summary_target_type,
)
from app.ai.nodes.chat.profile_helpers import _build_slot_question, _filled_slots
from app.ai.nodes.chat.slots import (
    RECOMMEND_WIZARD_FIELDS,
    SLOT_LABELS as _SLOT_LABELS,
    SLOT_OPTIONS as _SLOT_OPTIONS,
)
from app.ai.states.chat_state import ChatGraphState, Intent, PendingState, ProfileSlot

logger = logging.getLogger(__name__)


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

from __future__ import annotations

import logging
from typing import Any

from app.ai.nodes.chat.chat_nodes import _detect_assertive_phrases
from app.ai.nodes.chat.constants import (
    INTENT_TO_ACTION_TYPE as _INTENT_TO_ACTION_TYPE,
    INTENT_TO_API_ACTION as _INTENT_TO_API_ACTION,
)
from app.ai.states.chat_state import ChatGraphState, Intent
from app.services.chat.handlers._handler_result import HandlerResult

logger = logging.getLogger(__name__)


async def build_assistant_payload(
    state: ChatGraphState | HandlerResult,
    result: HandlerResult | None = None,
) -> dict[str, Any]:
    legacy_call = result is None
    if result is None:
        result = (
            state
            if isinstance(state, HandlerResult)
            else HandlerResult.from_state_patch(state)
        )

    decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": "missing"}
    intent: Intent = decision["intent"]
    is_prompt = bool(result.slot_request or result.profile_confirm)
    api_action = None if is_prompt else _INTENT_TO_API_ACTION.get(intent)
    content = result.content or ""
    disclaimer = False
    if (
        intent != "unclear"
        and not is_prompt
        and result.eligibility_result is None
    ):
        assertive = _detect_assertive_phrases(content)
        if assertive:
            logger.warning(
                "chat answer contains assertive phrases despite safety prompt",
                extra={"intent": intent, "phrases": assertive},
            )
            disclaimer = True

    policy_selection: dict | None = (
        {"intent": intent, "candidates": result.policy_candidates}
        if result.policy_candidates
        else None
    )

    payload = {
        "content": content,
        "user_status": result.user_status,
        "sources": [],
        "policies": result.policies,
        "evidences": result.evidences,
        "actions": [api_action] if api_action else [],
        "apply_card": result.apply_card,
        "easy_summary": result.easy_summary,
        "key_points": result.key_points,
        "disclaimer": disclaimer,
        "slot_request": result.slot_request,
        "profile_confirm": result.profile_confirm,
        "eligibility_result": result.eligibility_result,
        "suggested_actions": result.suggested_actions,
        "policy_selection": policy_selection,
    }
    return {"assistant_payload": payload} if legacy_call else payload


def extract_evidences(result: HandlerResult) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": e["chunk_id"],
            "snippet": e.get("snippet"),
            "evidence_role": e.get("evidence_role"),
        }
        for e in result.evidences
        if e.get("chunk_id") is not None
    ]


def extract_policy_links(
    state: ChatGraphState,
    result: HandlerResult,
) -> list[dict[str, Any]]:
    decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": ""}
    intent: Intent = decision["intent"]
    action_type = _INTENT_TO_ACTION_TYPE.get(intent)
    if action_type is None:
        return []
    return [
        {"policy_slug": p["slug"], "action_type": action_type}
        for p in result.policies
        if p.get("slug")
    ]

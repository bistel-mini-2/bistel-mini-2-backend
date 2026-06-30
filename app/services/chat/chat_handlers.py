from __future__ import annotations

import logging
from typing import Any

from app.ai.nodes.chat.chat_nodes import _generate_branch_answer, _generate_clarification_answer
from app.ai.states.chat_state import ChatGraphState
from app.services.chat.handlers._handler_apply import handle_apply
from app.services.chat.handlers._handler_recommend import handle_recommend
from app.services.chat.handlers._handler_slot import handle_collect_slots, handle_confirm_profile
from app.services.chat.handlers._handler_summary import handle_policy_summary, handle_summary
from app.services.chat.handlers._intent_classifier import classify_intent
from app.services.chat.ai._lifecycle_runners import (
    run_comparison_branch as _run_comparison_branch,
    run_eligibility_branch as _run_eligibility_branch,
)
from app.services.chat.handlers._payload_builders import (
    build_assistant_payload,
    extract_evidences,
    extract_policy_links,
)
from app.services.chat.ai._policy_resolver import (
    resolve_compare_targets as _resolve_compare_targets,
    resolve_single_policy_target as _resolve_single_policy_target,
)

logger = logging.getLogger(__name__)


async def handle_eligibility(
    state: ChatGraphState,
    db=None,
) -> dict[str, Any]:
    del db
    slug, policy_name, evidences = await _resolve_single_policy_target(
        state,
        intent="eligibility",
    )
    if slug is None:
        return {
            **state,
            "branch_content": await _generate_clarification_answer("eligibility", state),
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
            "branch_content": await _generate_clarification_answer("compare", state),
            "branch_policies": [],
            "branch_evidences": evidences,
        }
    return await _run_comparison_branch(
        state=state,
        slug_a=first[0],
        slug_b=second[0],
        evidences=evidences,
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

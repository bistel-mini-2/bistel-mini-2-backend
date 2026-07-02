from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.chat_nodes import (
    _build_apply_card,
    _generate_apply_answer,
    _generate_branch_answer,
    _generate_clarification_answer,
    _load_application_period_context,
    _run_apply_preparation,
)
from app.ai.nodes.chat.constants import (
    APPLY_MAX_RETRIES as _APPLY_MAX_RETRIES,
    APPLY_TEMPORARY_FAILURE_FALLBACK as _APPLY_TEMPORARY_FAILURE_FALLBACK,
)
from app.ai.states.chat_state import ChatGraphState
from app.services.chat.ai._policy_resolver import (
    resolve_single_policy_target as _resolve_single_policy_target,
)

logger = logging.getLogger(__name__)


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
            clarification = await _generate_clarification_answer("apply", s)
            return {
                **s,
                "apply_flow_status": "fallback_needed",
                "apply_error_message": clarification,
                "branch_content": clarification,
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

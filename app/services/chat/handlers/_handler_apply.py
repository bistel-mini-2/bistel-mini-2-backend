from __future__ import annotations

import logging

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
from app.services.chat.handlers._handler_result import HandlerResult

logger = logging.getLogger(__name__)

_APPLY_GENERIC_FALLBACK = "신청 안내를 준비하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."


async def handle_apply(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> HandlerResult:
    del db
    slug, policy_name, evidences, candidates = await _resolve_single_policy_target(
        state,
        intent="apply",
    )

    async def _branch() -> tuple[HandlerResult, str]:
        """(result, flow_status) 반환. flow_status: 'ok' | 'retryable_error' | 'fallback_needed'"""
        if slug is None:
            if candidates:
                from app.services.chat.chat_handlers import _build_policy_selection_response
                return _build_policy_selection_response(state, candidates, "apply", evidences), "ok"
            clarification = await _generate_clarification_answer("apply", state)
            return (
                HandlerResult(
                    content=clarification,
                    evidences=[],
                    pending={"intent": "apply", "kind": "clarification"},
                ),
                "fallback_needed",
            )

        apply_response, apply_error = await _run_apply_preparation(
            user_id=state["user_id"],
            policy_slug=slug,
        )
        if apply_error == "temporary_failure":
            return (
                HandlerResult(
                    content=_APPLY_TEMPORARY_FAILURE_FALLBACK,
                    evidences=evidences,
                ),
                "retryable_error",
            )

        if apply_response is None:
            content = await _generate_branch_answer("apply", state, evidences)
            return (
                HandlerResult(content=content, evidences=evidences),
                "ok",
            )

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
            state,
            evidences,
            apply_card,
            application_period_context,
        )
        return (
            HandlerResult(
                content=content,
                policies=apply_policies,
                evidences=evidences,
                apply_card=apply_card,
            ),
            "ok",
        )

    result, flow_status = await _branch()

    retry_count = 0
    while flow_status == "retryable_error":
        retry_count += 1
        if retry_count <= _APPLY_MAX_RETRIES:
            result, flow_status = await _branch()
        else:
            result = HandlerResult(
                content=result.content or _APPLY_GENERIC_FALLBACK,
            )
            break

    if flow_status == "fallback_needed" and not result.pending:
        result = HandlerResult(content=result.content or _APPLY_GENERIC_FALLBACK)

    return result

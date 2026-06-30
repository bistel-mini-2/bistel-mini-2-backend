from __future__ import annotations

import logging
from typing import Any

from app.ai.nodes.chat.chat_nodes import _load_policy_detail
from app.ai.nodes.chat.result_adapters import _adapt_policy_summary_result
from app.ai.states.chat_state import ChatGraphState
from app.services.chat.ai._graph_clients import get_policy_summary_graph
from app.services.chat.ai._policy_resolver import (
    resolve_single_policy_target as _resolve_single_policy_target,
)

logger = logging.getLogger(__name__)

_POLICY_SUMMARY_CLARIFICATION_FALLBACK = (
    "어떤 정책을 요약할까요? 정책명을 알려주시면 핵심 위주로 쉽게 정리해 드릴게요."
)


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
        summary_result = await get_policy_summary_graph().run(policy)
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

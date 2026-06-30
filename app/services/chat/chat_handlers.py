from __future__ import annotations

import logging
from typing import Any

from app.ai.nodes.chat import chat_nodes as _chat_nodes_module
from app.ai.nodes.chat.chat_nodes import (
    _generate_branch_answer,
    _generate_clarification_answer,
    _llm as _chat_llm,
)
from app.db.session import AsyncSessionLocal as _AsyncSessionLocal
from app.services.chat.ai import _graph_clients, _lifecycle_runners, _policy_resolver
from app.ai.states.chat_state import ChatGraphState
from app.services.chat.ai._graph_clients import (
    _COMPARISON_GRAPH as _DEFAULT_COMPARISON_GRAPH,
    _ELIGIBILITY_GRAPH as _DEFAULT_ELIGIBILITY_GRAPH,
    _LIFECYCLE_SERVICE as _DEFAULT_LIFECYCLE_SERVICE,
    _POLICY_SUMMARY_GRAPH as _DEFAULT_POLICY_SUMMARY_GRAPH,
    _RAG_SERVICE as _DEFAULT_RAG_SERVICE,
)
from app.services.chat.handlers import (
    _handler_apply,
    _handler_recommend,
    _handler_summary,
)
from app.services.chat.handlers._handler_slot import handle_collect_slots, handle_confirm_profile
from app.services.chat.handlers import _intent_classifier
from app.services.chat.ai._lifecycle_runners import (
    run_comparison_branch as _run_comparison_branch,
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

_llm = _chat_llm
AsyncSessionLocal = _AsyncSessionLocal
_RAG_SERVICE = _DEFAULT_RAG_SERVICE
_LIFECYCLE_SERVICE = _DEFAULT_LIFECYCLE_SERVICE
_ELIGIBILITY_GRAPH = _DEFAULT_ELIGIBILITY_GRAPH
_COMPARISON_GRAPH = _DEFAULT_COMPARISON_GRAPH
_POLICY_SUMMARY_GRAPH = _DEFAULT_POLICY_SUMMARY_GRAPH
_mark_recommendation_failed = _lifecycle_runners._mark_recommendation_failed
_run_eligibility_lifecycle = _lifecycle_runners.run_eligibility_lifecycle


def _sync_legacy_patch_points() -> None:
    llm = _llm if _llm is not _chat_llm else _chat_nodes_module._llm
    resolver_async_session_local = (
        AsyncSessionLocal
        if AsyncSessionLocal is not _AsyncSessionLocal
        else _policy_resolver.AsyncSessionLocal
    )
    lifecycle_async_session_local = (
        AsyncSessionLocal
        if AsyncSessionLocal is not _AsyncSessionLocal
        else _lifecycle_runners.AsyncSessionLocal
    )
    rag_service = (
        _RAG_SERVICE
        if _RAG_SERVICE is not _DEFAULT_RAG_SERVICE
        else _graph_clients._RAG_SERVICE
    )
    lifecycle_service = (
        _LIFECYCLE_SERVICE
        if _LIFECYCLE_SERVICE is not _DEFAULT_LIFECYCLE_SERVICE
        else _graph_clients._LIFECYCLE_SERVICE
    )
    eligibility_graph = (
        _ELIGIBILITY_GRAPH
        if _ELIGIBILITY_GRAPH is not _DEFAULT_ELIGIBILITY_GRAPH
        else _graph_clients._ELIGIBILITY_GRAPH
    )
    comparison_graph = (
        _COMPARISON_GRAPH
        if _COMPARISON_GRAPH is not _DEFAULT_COMPARISON_GRAPH
        else _graph_clients._COMPARISON_GRAPH
    )
    policy_summary_graph = (
        _POLICY_SUMMARY_GRAPH
        if _POLICY_SUMMARY_GRAPH is not _DEFAULT_POLICY_SUMMARY_GRAPH
        else _graph_clients._POLICY_SUMMARY_GRAPH
    )
    mark_recommendation_failed = (
        _mark_recommendation_failed
        if _mark_recommendation_failed is not _lifecycle_runners._mark_recommendation_failed
        else _lifecycle_runners._mark_recommendation_failed
    )
    run_eligibility_lifecycle = (
        _run_eligibility_lifecycle
        if _run_eligibility_lifecycle is not _lifecycle_runners.run_eligibility_lifecycle
        else _lifecycle_runners.run_eligibility_lifecycle
    )

    _intent_classifier._llm = llm
    _policy_resolver._llm = llm
    _policy_resolver.AsyncSessionLocal = resolver_async_session_local
    _lifecycle_runners.AsyncSessionLocal = lifecycle_async_session_local
    _lifecycle_runners._mark_recommendation_failed = mark_recommendation_failed
    _lifecycle_runners.run_eligibility_lifecycle = run_eligibility_lifecycle
    _graph_clients._RAG_SERVICE = rag_service
    _graph_clients._LIFECYCLE_SERVICE = lifecycle_service
    _graph_clients._ELIGIBILITY_GRAPH = eligibility_graph
    _graph_clients._COMPARISON_GRAPH = comparison_graph
    _graph_clients._POLICY_SUMMARY_GRAPH = policy_summary_graph


async def classify_intent(*args: Any, **kwargs: Any) -> ChatGraphState:
    _sync_legacy_patch_points()
    return await _intent_classifier.classify_intent(*args, **kwargs)


async def handle_eligibility(
    state: ChatGraphState,
    db=None,
) -> dict[str, Any]:
    _sync_legacy_patch_points()
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
    _sync_legacy_patch_points()
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


async def handle_apply(
    state: ChatGraphState,
    db=None,
) -> dict[str, Any]:
    _sync_legacy_patch_points()
    return await _handler_apply.handle_apply(state, db)


async def handle_recommend(
    state: ChatGraphState,
    db=None,
) -> dict[str, Any]:
    _sync_legacy_patch_points()
    return await _handler_recommend.handle_recommend(state, db)


async def handle_policy_summary(
    state: ChatGraphState,
) -> dict[str, Any]:
    _sync_legacy_patch_points()
    return await _handler_summary.handle_policy_summary(state)


async def handle_summary(
    state: ChatGraphState,
) -> dict[str, Any]:
    _sync_legacy_patch_points()
    return await _handler_summary.handle_summary(state)


async def _run_eligibility_branch(
    *,
    state: ChatGraphState,
    policy_slug: str,
    policy_name: str | None,
    evidences: list[dict],
) -> ChatGraphState:
    _sync_legacy_patch_points()
    return await _lifecycle_runners.run_eligibility_branch(
        state=state,
        policy_slug=policy_slug,
        policy_name=policy_name,
        evidences=evidences,
    )


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

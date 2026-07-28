from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.chat_nodes import (
    _attach_recommendation_context,
    _generate_branch_answer,
    _recommend_follow_up_already_asked,
)
from app.ai.nodes.chat.constants import (
    RECOMMEND_FALLBACK_ERROR as _RECOMMEND_FALLBACK_ERROR,
    RECOMMEND_FALLBACK_FOLLOW_UP as _RECOMMEND_FALLBACK_FOLLOW_UP,
    RECOMMEND_FOLLOW_UP_LIMIT_REACHED as _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
    RECOMMEND_MAX_RETRIES as _RECOMMEND_MAX_RETRIES,
)
from app.ai.nodes.chat.profile_helpers import _filled_slots, _profile_to_selected_conditions
from app.ai.nodes.chat.result_adapters import _adapt_recommendation_result
from app.ai.nodes.chat.slots import (
    RECOMMEND_WIZARD_FIELDS as _RECOMMEND_WIZARD_FIELDS,
    SLOT_LABELS as _SLOT_LABELS,
    SLOT_OPTIONS as _SLOT_OPTIONS,
)
from app.ai.states.chat_state import ChatGraphState, PendingState
from app.common.ai_status import RequestStatus
from app.services.chat.ai._lifecycle_runners import (
    run_recommendation_lifecycle as _run_recommendation_lifecycle,
)
from app.services.chat.handlers._handler_result import HandlerResult

logger = logging.getLogger(__name__)


async def handle_recommend(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> HandlerResult:
    del db
    selected_conditions = _profile_to_selected_conditions(state.get("profile"))
    follow_up_already_asked = _recommend_follow_up_already_asked(
        state.get("slot")
    ) or bool((state.get("profile") or {}).get("db_profile_confirmed"))

    async def _branch(retry_count: int = 0) -> tuple[HandlerResult, str]:
        """(result, flow_status) 반환. flow_status: 'ok' | 'retryable_error' | 'fallback_needed'"""
        snapshot, lifecycle_error = await _run_recommendation_lifecycle(
            user_id=state["user_id"],
            user_content=state["user_content"],
            selected_conditions=selected_conditions or None,
            follow_up_resolved=follow_up_already_asked,
        )
        if lifecycle_error == "temporary_failure":
            return (
                HandlerResult(content=_RECOMMEND_FALLBACK_ERROR),
                "retryable_error",
            )
        if snapshot is None:
            return (
                HandlerResult(content=_RECOMMEND_FALLBACK_ERROR),
                "fallback_needed",
            )
        if snapshot.status == RequestStatus.FOLLOW_UP_REQUIRED:
            if follow_up_already_asked:
                return (
                    HandlerResult(
                        content=_RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
                        slot_request=None,
                        pending=None,
                    ),
                    "fallback_needed",
                )
            questions = snapshot.questions or []
            if questions:
                engine_q_map: dict[str, dict] = {
                    str(q.get("field_name") or f"follow_up_{i + 1}"): q
                    for i, q in enumerate(questions)
                }
                filled = _filled_slots(state.get("profile"))
                extra_keys = [
                    f for f in _RECOMMEND_WIZARD_FIELDS
                    if f not in filled and f not in engine_q_map
                ]
                awaiting = list(engine_q_map.keys()) + extra_keys
                fields = [
                    {
                        "key": key,
                        "label": (
                            engine_q_map[key].get("question_text")
                            if key in engine_q_map
                            else None
                        ) or _SLOT_LABELS.get(key, key),
                        "options": _SLOT_OPTIONS.get(key, []),
                    }
                    for key in awaiting
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
                    "fields": fields,
                }
                pending: PendingState = {
                    "intent": "recommend",
                    "awaiting": awaiting,
                    "asked": [awaiting[0]] if awaiting else [],
                    "kind": "slot",
                }
                return (
                    HandlerResult(
                        content="맞춤 추천을 위해 정보가 조금 더 필요해요.",
                        slot_request=slot_request,
                        pending=pending,
                    ),
                    "ok",
                )
            return (
                HandlerResult(content=_RECOMMEND_FALLBACK_FOLLOW_UP),
                "fallback_needed",
            )
        if snapshot.status != RequestStatus.COMPLETED:
            return (
                HandlerResult(content=_RECOMMEND_FALLBACK_ERROR),
                "fallback_needed",
            )
        policies, evidences = _adapt_recommendation_result(snapshot.result_json)
        policies = _attach_recommendation_context(
            policies,
            request_id=snapshot.request_id,
            selected_conditions=selected_conditions,
            merged_condition_json=snapshot.merged_condition_json or selected_conditions,
        )
        content = await _generate_branch_answer("recommend", state, evidences)
        return (
            HandlerResult(content=content, policies=policies, evidences=evidences),
            "ok",
        )

    result, flow_status = await _branch()

    retry_count = 0
    while flow_status == "retryable_error":
        retry_count += 1
        if retry_count <= _RECOMMEND_MAX_RETRIES:
            result, flow_status = await _branch(retry_count)
        else:
            result = HandlerResult(content=result.content or _RECOMMEND_FALLBACK_ERROR)
            break

    if flow_status == "fallback_needed":
        result = HandlerResult(content=result.content or _RECOMMEND_FALLBACK_ERROR)

    return result

from __future__ import annotations

import logging
from typing import Any

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

logger = logging.getLogger(__name__)


async def handle_recommend(
    state: ChatGraphState,
    db: AsyncSession | None = None,
) -> dict[str, Any]:
    del db
    selected_conditions = _profile_to_selected_conditions(state.get("profile"))
    follow_up_already_asked = _recommend_follow_up_already_asked(
        state.get("slot")
    ) or bool((state.get("profile") or {}).get("db_profile_confirmed"))

    async def _branch(s: ChatGraphState) -> ChatGraphState:
        snapshot, lifecycle_error = await _run_recommendation_lifecycle(
            user_id=s["user_id"],
            user_content=s["user_content"],
            selected_conditions=selected_conditions or None,
            follow_up_resolved=follow_up_already_asked,
        )
        if lifecycle_error == "temporary_failure":
            return {
                **s,
                "recommend_flow_status": "retryable_error",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "recommend_max_retries": _RECOMMEND_MAX_RETRIES,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot is None:
            return {
                **s,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot.status == RequestStatus.FOLLOW_UP_REQUIRED:
            if follow_up_already_asked:
                return {
                    **s,
                    "recommend_flow_status": "fallback_needed",
                    "recommend_error_message": _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
                    "branch_content": _RECOMMEND_FOLLOW_UP_LIMIT_REACHED,
                    "branch_policies": [],
                    "branch_evidences": [],
                    "branch_apply_card": None,
                    "slot_request": None,
                    "pending": None,
                }
            questions = snapshot.questions or []
            if questions:
                # 엔진이 요청한 질문 목록
                engine_q_map: dict[str, dict] = {
                    str(q.get("field_name") or f"follow_up_{i + 1}"): q
                    for i, q in enumerate(questions)
                }
                # 이미 채워진 슬롯을 제외한 나머지 위저드 필드를 함께 표시 (한 번에 모아서 받기)
                filled = _filled_slots(s.get("profile"))
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
                return {
                    **s,
                    "recommend_flow_status": "ok",
                    "recommend_error_message": None,
                    "branch_content": "맞춤 추천을 위해 정보가 조금 더 필요해요.",
                    "branch_policies": [],
                    "branch_evidences": [],
                    "branch_apply_card": None,
                    "slot_request": slot_request,
                    "pending": pending,
                }
            return {
                **s,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_content": _RECOMMEND_FALLBACK_FOLLOW_UP,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        if snapshot.status != RequestStatus.COMPLETED:
            return {
                **s,
                "recommend_flow_status": "fallback_needed",
                "recommend_error_message": _RECOMMEND_FALLBACK_ERROR,
                "branch_content": _RECOMMEND_FALLBACK_ERROR,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
        policies, evidences = _adapt_recommendation_result(snapshot.result_json)
        policies = _attach_recommendation_context(
            policies,
            request_id=snapshot.request_id,
            selected_conditions=selected_conditions,
            merged_condition_json=snapshot.merged_condition_json or selected_conditions,
        )
        content = await _generate_branch_answer("recommend", s, evidences)
        return {
            **s,
            "recommend_flow_status": "ok",
            "recommend_error_message": None,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    current: ChatGraphState = await _branch(state)

    while current.get("recommend_flow_status") == "retryable_error":
        retry_count = int(current.get("recommend_retry_count") or 0) + 1
        current = {**current, "recommend_retry_count": retry_count}
        max_retries = int(current.get("recommend_max_retries") or 1)
        if retry_count <= max_retries:
            current = await _branch(current)
        else:
            fallback_message = current.get("recommend_error_message") or _RECOMMEND_FALLBACK_ERROR
            current = {
                **current,
                "recommend_flow_status": "ok",
                "branch_content": fallback_message,
                "branch_policies": [],
                "branch_evidences": [],
                "branch_apply_card": None,
            }
            break

    if current.get("recommend_flow_status") == "fallback_needed":
        fallback_message = current.get("recommend_error_message") or _RECOMMEND_FALLBACK_ERROR
        current = {
            **current,
            "recommend_flow_status": "ok",
            "branch_content": fallback_message,
            "branch_policies": [],
            "branch_evidences": [],
            "branch_apply_card": None,
        }

    return current

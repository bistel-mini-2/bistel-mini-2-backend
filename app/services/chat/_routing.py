import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chat.ai._follow_up import attach_similar_policies
from app.services.chat.handlers._quality_validator import validate_branch_result
from app.services.chat.persistence._persistence import fallback_payload

logger = logging.getLogger(__name__)

_INTENT_TO_SSE: dict[str, str] = {
    "recommend": "recommendation",
    "eligibility": "eligibility",
    "compare": "comparison",
    "policy_summary": "policy_summary",
}


async def run_chat(
    *,
    db: AsyncSession,
    user_id: int,
    user_content: str,
    history: list[dict],
    slot: dict | None = None,
    recent_assistant_policy: dict | None = None,
    emit_intent: bool = False,
    on_intent: Callable[[str], Awaitable[None]] | None = None,
    on_token: Callable[[str], Awaitable[None]] | None = None,
    on_progress: Callable[..., Awaitable[None]] | None = None,
    preseed_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    처리 흐름:
      메시지 수신 (user_content, slot, history)
      → 세션·슬롯·최근 이력 로드 (caller에서 전달됨)
      → 진행 상태 확인 + 구조화된 의도 분류 (classify_intent)
          ├─ profile_confirm fast-path (LLM 생략)
          ├─ LLM 구조화 출력 (intent, secondary_intents, confidence, ...)
          └─ Python 검증 및 보정
      → 모호성 확인 (profile_confirm / awaiting_slots 여부)
      → Handler 하나 실행
      → 공통 품질 검증 (validate_branch_result)
      → 공통 Payload 생성 (build_assistant_payload)
      → evidences / policy_links 추출
      → 반환 (메시지·슬롯 저장은 caller에서 일괄 처리)
    """
    from app.ai.nodes.chat.chat_nodes import (
        reset_branch_token_callback,
        set_branch_token_callback,
    )
    from app.ai.utils.progress import reset_progress_callback, set_progress_callback
    from app.services.chat.chat_handlers import (
        build_assistant_payload,
        classify_intent,
        extract_evidences,
        extract_policy_links,
        handle_apply,
        handle_collect_slots,
        handle_compare,
        handle_confirm_profile,
        handle_eligibility,
        handle_policy_summary,
        handle_recommend,
        handle_summary,
        handle_unclear,
    )

    try:
        # ── 진행 상태 확인 + 의도 분류 ──────────────────────────────────────
        state = await classify_intent(
            user_id=user_id,
            user_content=user_content,
            history=history,
            slot=slot or {},
            recent_assistant_policy=recent_assistant_policy,
        )

        decision = state.get("supervisor_decision") or {"intent": "unclear"}
        intent = decision.get("intent", "unclear")
        if emit_intent and on_intent is not None:
            await on_intent(_INTENT_TO_SSE.get(intent, "general"))

        token = set_branch_token_callback(on_token)
        progress_token = set_progress_callback(on_progress)
        try:
            # ── Handler 하나 실행 ──────────────────────────────────────────
            if state.get("profile_confirm"):
                branch_result = await handle_confirm_profile(state)
            elif state.get("awaiting_slots"):
                branch_result = await handle_collect_slots(state)
            else:
                match intent:
                    case "recommend":
                        branch_result = await handle_recommend(state, db)
                    case "eligibility":
                        branch_result = await handle_eligibility(state, db)
                    case "compare":
                        branch_result = await handle_compare(state)
                    case "apply":
                        branch_result = await handle_apply(state, db)
                    case "policy_summary":
                        branch_result = await handle_policy_summary(state)
                    case "summary":
                        branch_result = await handle_summary(state)
                    case _:
                        branch_result = await handle_unclear(state)
        finally:
            reset_branch_token_callback(token)
            reset_progress_callback(progress_token)

        state.update(branch_result)

        # ── 공통 품질 검증 ────────────────────────────────────────────────
        correction = validate_branch_result(state, intent)
        if correction is not None:
            state.update(correction)

        # ── Payload 생성 ──────────────────────────────────────────────────
        state.update(await build_assistant_payload(state))
        if decision.get("similar_policy_requested"):
            await attach_similar_policies(db, state)

        return {
            **(preseed_result or {}),
            **state,
            "evidences_to_save": await extract_evidences(state),
            "policy_links_to_save": await extract_policy_links(state),
        }
    except Exception:
        logger.exception("Chat direct routing failed")
        return {
            "assistant_payload": fallback_payload(),
            "supervisor_decision": {"intent": "unclear", "raw": "routing_error"},
            "evidences_to_save": [],
            "policy_links_to_save": [],
        }

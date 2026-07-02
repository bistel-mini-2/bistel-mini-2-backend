from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.ai.nodes.chat.chat_nodes import (
    _find_slot_policy_by_slug,
    _format_slot_context,
    _history_to_lc_messages,
    _IntentDecision,
    _llm,
    _load_db_profile_summary,
)
from app.ai.nodes.chat.profile_helpers import (
    _format_profile_context,
    _interpret_confirm,
    _merge_profile,
    _missing_required,
)
from app.ai.nodes.chat.prompts import SUPERVISOR_SYSTEM_TEMPLATE
from app.ai.nodes.chat.slots import CONFIRM_OPTIONS, REQUIRED_SLOTS
from app.ai.states.chat_state import (
    ChatGraphState,
    ChatSlot,
    HistoryMessage,
    Intent,
    PendingState,
    ProfileSlot,
)

logger = logging.getLogger(__name__)

# intent별 API 액션 이름 매핑 (suggested_actions 생성용)
_INTENT_TO_API_ACTION: dict[Intent, str] = {
    "recommend": "recommend",
    "eligibility": "eligibility",
    "compare": "compare",
    "apply": "apply",
}


def _validate_llm_decision(
    decision: _IntentDecision,
    slot: ChatSlot | dict | None,
    pending: PendingState | None,
) -> _IntentDecision:
    """LLM 결과를 Python 규칙으로 검증·보정한다.

    검증 항목:
    - 매우 낮은 confidence + pending 없음 → unclear
    - 낮은 confidence + slot-filling 중 + 슬롯 미답변 → pending 의도 유지

    Note: resolved_policy_slug 검증은 호출 전에 슬롯 존재 여부로 이미 처리됨.
    """
    intent = decision.intent

    # Rule 1: 매우 낮은 확신도 + pending 없음 → unclear 처리
    if decision.confidence < 0.5 and not pending:
        logger.info(
            "quality_check: confidence=%.2f too low with no pending; falling back to unclear",
            decision.confidence,
        )
        decision = decision.model_copy(
            update={"intent": "unclear", "ambiguity_reason": decision.ambiguity_reason or "낮은 확신도"}
        )

    # Rule 2: slot-filling 진행 중 + 낮은 확신도 + 슬롯 미답변 → pending 의도 유지
    if (
        pending
        and pending.get("kind") == "slot"
        and decision.confidence < 0.6
        and not decision.extracted_profile
    ):
        pending_intent = pending.get("intent")
        if pending_intent and intent not in (pending_intent, "unclear"):
            logger.info(
                "quality_check: low confidence=%.2f during slot-filling; resuming pending intent=%s",
                decision.confidence,
                pending_intent,
            )
            decision = decision.model_copy(update={"intent": pending_intent})

    return decision


def _build_suggested_actions(secondary_intents: list[Intent]) -> list[str]:
    """secondary_intents를 API 액션 이름 목록으로 변환한다."""
    return [
        _INTENT_TO_API_ACTION[si]
        for si in secondary_intents
        if si in _INTENT_TO_API_ACTION
    ]


async def classify_intent(
    *,
    user_content: str,
    history: list[HistoryMessage],
    slot: ChatSlot | dict | None,
    recent_assistant_policy: dict[str, Any] | None,
    user_id: int,
) -> ChatGraphState:
    """Classify intent and prepare slot/profile routing state.

    처리 순서:
    1. 슬롯에서 현재 진행 상태 로드
    2. Fast-path: profile_confirm 상태에서 명확한 yes/no 응답이면 LLM 생략
    3. LLM 구조화 출력으로 의도 분류
    4. Python 검증 및 보정
    5. 진행 상태 분기 (confirm / clarification / slot / interrupt)
    6. 슬롯 필링 필요 여부 결정
    """
    state: ChatGraphState = {
        "user_id": user_id,
        "user_content": user_content,
        "history": history,
        "slot": slot or {},
        "recent_assistant_policy": recent_assistant_policy,
    }

    slot_state = state.get("slot")
    profile: ProfileSlot = (slot_state or {}).get("profile") or {}
    pending: PendingState | None = (slot_state or {}).get("pending") or None
    prev_kind = (pending or {}).get("kind") if pending else None

    # ── 1. Fast-path: profile_confirm 상태에서 명확한 yes/no ───────────────────
    if prev_kind == "confirm":
        answer = _interpret_confirm(user_content)
        if answer in ("yes", "no"):
            intent: Intent = "recommend"
            raw = f'{{"intent": "recommend", "fastpath": "profile_confirm"}}'
            if answer == "yes":
                awaiting: list[str] = []
                profile = {**profile, "db_profile_confirmed": True}
            else:
                awaiting = list(REQUIRED_SLOTS.get("recommend", ()))
            pending_active = bool(awaiting)
            logger.info("chat_profile_confirm_fastpath", extra={"answer": answer})
            return {
                **state,
                "profile": profile,
                "pending_intent": intent if pending_active else None,
                "awaiting_slots": awaiting,
                "profile_confirm": None,
                "supervisor_decision": {
                    "intent": intent,
                    "raw": raw,
                    "resolved_policy_slug": None,
                    "secondary_intents": [],
                    "is_context_dependent": False,
                    "confidence": 1.0,
                    "ambiguity_reason": None,
                },
            }
        # answer == None: 불명확한 응답이면 LLM으로 계속 진행

    # ── 2. LLM 구조화 출력으로 의도 분류 ──────────────────────────────────────
    llm = _llm().with_structured_output(_IntentDecision)
    slot_context = _format_slot_context(slot_state)
    profile_context = _format_profile_context(profile)
    system_prompt = SUPERVISOR_SYSTEM_TEMPLATE.format(
        slot_context=slot_context, profile_context=profile_context
    )
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    messages.extend(_history_to_lc_messages(state["history"]))
    messages.append(HumanMessage(content=state["user_content"]))

    resolved_slug: str | None = None
    extracted: dict[str, Any] | None = None
    raw = "{}"
    secondary_intents: list[Intent] = []
    is_context_dependent = False
    confidence = 0.8
    ambiguity_reason: str | None = None

    try:
        decision = await llm.ainvoke(messages)
        intent = decision.intent
        raw = decision.model_dump_json()

        # slug 검증: 슬롯에 실제 존재하는 경우에만 사용
        candidate = decision.resolved_policy_slug
        if candidate and _find_slot_policy_by_slug(slot_state, candidate):
            resolved_slug = candidate

        if decision.extracted_profile is not None:
            extracted = decision.extracted_profile.model_dump(exclude_none=True)

        secondary_intents = list(decision.secondary_intents or [])
        is_context_dependent = bool(decision.is_context_dependent)
        confidence = float(decision.confidence)
        ambiguity_reason = decision.ambiguity_reason

        # ── 3. Python 검증 및 보정 ─────────────────────────────────────────────
        # resolved_slug는 이미 위에서 슬롯 존재 여부로 검증했으므로 유지
        decision = _validate_llm_decision(decision, slot_state, pending)
        intent = decision.intent
        # slug는 위 슬롯 검증 결과를 사용 (decision.resolved_policy_slug는 보정 전 원본일 수 있음)
        confidence = float(decision.confidence)
        ambiguity_reason = decision.ambiguity_reason

    except Exception as exc:
        logger.exception("Intent classification failed; falling back to unclear")
        intent = "unclear"
        raw = f"error: {exc}"

    profile = _merge_profile(profile, extracted)

    # ── 4. 진행 상태 분기 ─────────────────────────────────────────────────────
    profile_confirm: dict[str, Any] | None = None
    awaiting = []

    if prev_kind == "confirm" and intent in ("unclear", "recommend"):
        # Fast-path를 통과 못 했을 때 (answer=None): 기존 확인 프롬프트 재사용
        intent = "recommend"
        answer = _interpret_confirm(state["user_content"])
        if answer == "yes":
            awaiting = []
            profile = {**profile, "db_profile_confirmed": True}
        elif answer == "no":
            awaiting = list(REQUIRED_SLOTS.get("recommend", ()))
        else:
            awaiting = _missing_required("recommend", profile)
        logger.info("chat_profile_confirm_resume", extra={"answer": answer})

    elif prev_kind == "clarification" and pending and pending.get("intent"):
        # clarification pending: LLM 분류 무시하고 이전 의도 강제 이어받기
        intent = pending["intent"]
        logger.info("chat_clarification_resume", extra={"pending_intent": intent})

    else:
        # 일반 slot-filling 재개 또는 새 의도로 전환
        resumed = False
        if pending and pending.get("intent") and prev_kind != "confirm":
            pending_intent = pending["intent"]
            pending_awaiting = pending.get("awaiting") or list(
                REQUIRED_SLOTS.get(pending_intent, ())
            )
            answered_slot = bool(extracted) and any(
                key in (extracted or {}) for key in pending_awaiting
            )

            # 사용자가 새로운 명확한 의도로 전환했는지 판단
            # 전환 조건: LLM이 다른 intent를 확신 있게 분류했고, 슬롯 답변도 아닌 경우
            topic_switched = (
                intent not in ("unclear", pending_intent)
                and not answered_slot
                and confidence >= 0.7
            )

            if topic_switched:
                # 새 의도로 전환 → pending 상태 해제
                logger.info(
                    "chat_topic_switch",
                    extra={
                        "from_intent": pending_intent,
                        "to_intent": intent,
                        "confidence": confidence,
                    },
                )
            elif intent in ("unclear", pending_intent) or answered_slot:
                intent = pending_intent
                resumed = True
                unfilled = _missing_required(intent, profile)
                if unfilled:
                    asked = pending.get("awaiting") or unfilled
                    skipped = set(profile.get("skipped") or [])
                    skipped.update(s for s in unfilled if s in asked)
                    profile = {**profile, "skipped": sorted(skipped)}

            logger.info(
                "chat_pending_resume",
                extra={
                    "pending_intent": pending_intent,
                    "resumed": resumed,
                    "topic_switched": topic_switched,
                },
            )

        awaiting = _missing_required(intent, profile)
        if intent == "recommend" and awaiting:
            summary = await _load_db_profile_summary(state["user_id"])
            if summary:
                profile_confirm = {"summary": summary, "options": CONFIRM_OPTIONS}
                awaiting = []

    # ── 5. suggested_actions 생성 (복합 의도 처리) ────────────────────────────
    # pending 상태 중이거나 슬롯 필링/confirm 중이면 secondary_intents 무시
    suggested_actions = (
        _build_suggested_actions(secondary_intents)
        if not (awaiting or profile_confirm)
        else []
    )

    pending_active = bool(awaiting or profile_confirm)
    return {
        **state,
        "profile": profile,
        "pending_intent": intent if pending_active else None,
        "awaiting_slots": awaiting,
        "profile_confirm": profile_confirm,
        "branch_suggested_actions": suggested_actions,
        "supervisor_decision": {
            "intent": intent,
            "raw": raw,
            "resolved_policy_slug": resolved_slug,
            "secondary_intents": secondary_intents,
            "is_context_dependent": is_context_dependent,
            "confidence": confidence,
            "ambiguity_reason": ambiguity_reason,
        },
    }

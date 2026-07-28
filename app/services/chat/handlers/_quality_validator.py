"""
공통 품질 검증 모듈.

Handler 결과가 build_assistant_payload()를 거치기 전에 결정론적 Python 규칙으로 검증한다.
LLM 검증은 이 인터페이스를 통해 선택적으로 추가할 수 있도록 분리되어 있다.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any

from app.ai.states.chat_state import Intent
from app.services.chat.handlers._handler_result import HandlerResult

logger = logging.getLogger(__name__)

_ASSERTIVE_ELIGIBILITY_PHRASES = (
    "받을 수 있습니다",
    "받을 수 있어요",
    "받으실 수 있습니다",
    "신청 가능합니다",
    "신청하실 수 있습니다",
    "지원받을 수 있습니다",
    "지원받으실 수 있습니다",
    "대상입니다",
    "해당됩니다",
)

_SAFE_FALLBACK_CONTENT = (
    "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)


def validate_branch_result(
    state: dict[str, Any],
    intent: Intent,
    result: HandlerResult | None = None,
) -> HandlerResult | dict[str, Any] | None:
    """
    결정론적 품질 검증. 문제 발견 시 보정된 HandlerResult를 반환하고, 문제 없으면 None을 반환한다.

    검증 항목:
    - 빈 응답 텍스트
    - 구조화된 eligibility 결과 없이 자격 확정 표현 사용
    - 후속 행동이 현재 의도와 동일 (중복 제거)
    """
    legacy_call = result is None
    result = result or HandlerResult.from_state_patch(state)

    if result.slot_request or result.profile_confirm:
        return None

    content = result.content or ""
    issues: list[str] = []
    corrections: dict[str, Any] = {}

    # 1. 빈 응답 텍스트 → 안전한 fallback
    if not content.strip():
        issues.append("empty_content")
        corrections["content"] = _SAFE_FALLBACK_CONTENT
        logger.warning(
            "quality_validation: empty content for intent=%s; using fallback",
            intent,
        )

    # 2. eligibility: 구조화된 결과 없이 확정 표현 → 경고만 (disclaimer는 payload 단계에서 처리)
    if (
        intent == "eligibility"
        and result.eligibility_result is None
        and not result.policy_candidates
        and content.strip()
    ):
        for phrase in _ASSERTIVE_ELIGIBILITY_PHRASES:
            if phrase in content:
                issues.append("assertive_without_eligibility_result")
                logger.warning(
                    "quality_validation: assertive phrase '%s' used without eligibility result",
                    phrase,
                )
                break

    # 3. suggested_actions에서 primary intent와 동일한 항목 제거 (중복 방지)
    # classifier_suggested_actions(분류기 출력)와 result.suggested_actions를 병합
    classifier_suggested = list(
        state.get("classifier_suggested_actions")
        or state.get("branch_suggested_actions")
        or []
    )
    merged_suggested = classifier_suggested + [
        a for a in result.suggested_actions if a not in classifier_suggested
    ]
    cleaned_suggested = [a for a in merged_suggested if a != intent]
    if cleaned_suggested != result.suggested_actions:
        issues.append("suggested_actions_merged_or_deduplicated")
        corrections["suggested_actions"] = cleaned_suggested

    if not issues:
        # suggested_actions 병합만 필요한 경우에도 반영
        if cleaned_suggested != result.suggested_actions:
            return replace(result, suggested_actions=cleaned_suggested)
        return None

    corrected = replace(result, **corrections)
    if not legacy_call:
        return corrected
    return {
        **state,
        **corrected.to_state_patch(),
        "_validation_issues": issues,
    }


async def validate_branch_result_with_llm(
    state: dict[str, Any],
    intent: Intent,
    result: HandlerResult,
    *,
    llm_check_enabled: bool = False,
) -> HandlerResult | None:
    """
    LLM 기반 주관적 품질 검증 인터페이스 (선택적).
    llm_check_enabled=False이면 결정론적 검증만 실행한다.
    """
    validated = validate_branch_result(state, intent, result)
    if not llm_check_enabled:
        return validated
    # TODO: LLM 품질 검증이 필요할 경우 여기에 구현
    return validated

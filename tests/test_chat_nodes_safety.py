import asyncio
import logging

import pytest

from app.ai.nodes.chat import chat_nodes
from app.ai.nodes.chat.chat_nodes import (
    _BRANCH_SYSTEM_PROMPTS,
    _COMMON_SAFETY_RULES,
    ChatGraphNodes,
    _detect_assertive_phrases,
)


@pytest.mark.parametrize(
    "intent",
    ["recommend", "eligibility", "compare", "apply", "policy_summary"],
)
def test_branch_prompts_prepend_common_safety_rules(intent: str) -> None:
    prompt = _BRANCH_SYSTEM_PROMPTS[intent]
    assert prompt.startswith(_COMMON_SAFETY_RULES)


def test_unclear_branch_prompt_omits_safety_rules() -> None:
    assert _COMMON_SAFETY_RULES not in _BRANCH_SYSTEM_PROMPTS["unclear"]


def test_detect_assertive_phrases_returns_hits() -> None:
    content = "신청 가능합니다. 조건만 맞으면 받을 수 있습니다."
    hits = _detect_assertive_phrases(content)
    assert "신청 가능합니다" in hits
    assert "받을 수 있습니다" in hits


def test_detect_assertive_phrases_returns_empty_for_safe_text() -> None:
    content = "조건에 맞으면 해당될 수 있어요. 주민센터에서 확인해 보세요."
    assert _detect_assertive_phrases(content) == []


def _build_state(*, intent: str, content: str) -> dict:
    return {
        "user_id": 1,
        "user_content": "테스트",
        "history": [],
        "supervisor_decision": {"intent": intent, "raw": "{}"},
        "branch_content": content,
        "branch_policies": [],
        "branch_evidences": [],
    }


def test_assistant_payload_logs_warning_on_assertive_phrase(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nodes = ChatGraphNodes(rag_service=object())
    state = _build_state(intent="eligibility", content="당신은 대상입니다.")

    with caplog.at_level(logging.WARNING, logger=chat_nodes.__name__):
        result = asyncio.run(nodes.assistant_payload_build(state))

    assert any(
        "assertive phrases" in record.message for record in caplog.records
    )
    assert result["assistant_payload"]["disclaimer"] is True


def test_assistant_payload_no_warning_when_content_safe(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nodes = ChatGraphNodes(rag_service=object())
    state = _build_state(
        intent="eligibility",
        content="조건에 맞으면 해당될 수 있어요. 주민센터에서 확인해 보세요.",
    )

    with caplog.at_level(logging.WARNING, logger=chat_nodes.__name__):
        asyncio.run(nodes.assistant_payload_build(state))

    assert not any(
        "assertive phrases" in record.message for record in caplog.records
    )


def test_assistant_payload_skips_detection_for_unclear(
    caplog: pytest.LogCaptureFixture,
) -> None:
    nodes = ChatGraphNodes(rag_service=object())
    state = _build_state(intent="unclear", content="당신은 대상입니다.")

    with caplog.at_level(logging.WARNING, logger=chat_nodes.__name__):
        result = asyncio.run(nodes.assistant_payload_build(state))

    assert not any(
        "assertive phrases" in record.message for record in caplog.records
    )
    assert result["assistant_payload"]["disclaimer"] is False

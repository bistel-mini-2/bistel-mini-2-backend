import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.nodes.chat import chat_nodes
from app.services.chat_handlers import handle_unclear, build_assistant_payload


def _fake_llm(monkeypatch: pytest.MonkeyPatch, response: str = "무슨 말씀이신지요?") -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=response))
    monkeypatch.setattr(chat_nodes, "_llm", lambda: llm)
    return llm


def test_branch_unclear_calls_llm_and_returns_empty_policies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _fake_llm(monkeypatch)

    state = {
        "user_id": 1,
        "user_content": "뭔가 하고 싶어요",
        "history": [],
        "supervisor_decision": {"intent": "unclear", "raw": "{}"},
    }

    result = asyncio.run(handle_unclear(state))

    llm.ainvoke.assert_awaited_once()
    assert result["branch_content"] == "무슨 말씀이신지요?"
    assert result["branch_policies"] == []
    assert result["branch_evidences"] == []


def test_branch_unclear_disclaimer_is_false_in_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, response="무엇을 도와드릴까요?")

    state = {
        "user_id": 1,
        "user_content": "안녕",
        "history": [],
        "supervisor_decision": {"intent": "unclear", "raw": "{}"},
        "branch_content": "무엇을 도와드릴까요?",
        "branch_policies": [],
        "branch_evidences": [],
    }

    branch_result = asyncio.run(handle_unclear(state))
    payload_state = asyncio.run(build_assistant_payload(branch_result))

    assert payload_state["assistant_payload"]["disclaimer"] is False
    assert payload_state["assistant_payload"]["actions"] == []

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from app.ai.nodes.chat import chat_nodes
from app.repositories.policy_repository import PolicyRepository
from app.services import chat_handlers
from app.services.chat_handlers import handle_summary


class _FakeRagResult:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


class _FakeRagService:
    def __init__(self, results: list[Any]) -> None:
        self.search = AsyncMock(return_value=_FakeRagResult(results))


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


def _rag_chunk(*, chunk_id: int, policy_code: str, policy_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        policy_code=policy_code,
        policy_name=policy_name,
        source_url=None,
        chunk_text="요약 참고 텍스트",
    )


def _fake_llm(monkeypatch: Any, response: str = "요약 답변입니다.") -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=response))
    monkeypatch.setattr(chat_nodes, "_llm", lambda: llm)
    return llm


def _policy() -> dict[str, Any]:
    return {
        "policy_id": 100,
        "slug": "WLF1",
        "name": "임신·출산 진료비",
        "target_description": "임신부",
        "benefit_description": "진료비 지원",
    }


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())


def test_branch_summary_resolved_slug_uses_policy_name_for_rag(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="임신·출산 진료비")]
    )
    graph = MagicMock()
    graph.run = AsyncMock(return_value={"summary": "요약 답변입니다."})
    monkeypatch.setattr(PolicyRepository, "find_policy_detail", AsyncMock(return_value=_policy()))
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    state = {
        "user_id": 7,
        "user_content": "이 정책 요약해줘",
        "history": [],
        "supervisor_decision": {
            "intent": "summary",
            "raw": "{}",
            "resolved_policy_slug": "WLF1",
        },
        "slot": {
            "recent_policies": [
                {
                    "slug": "WLF1",
                    "policy_name": "임신·출산 진료비",
                    "last_action": "RECOMMENDED",
                }
            ]
        },
    }

    result = asyncio.run(handle_summary(state))

    rag.search.assert_not_awaited()
    graph.run.assert_awaited_once()
    assert result["branch_content"] == "요약 답변입니다."
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_summary_no_target_asks_policy_name(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=2, policy_code="WLF2", policy_name="산모 건강관리")]
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    state = {
        "user_id": 7,
        "user_content": "지원 내용 요약해줘",
        "history": [],
        "supervisor_decision": {"intent": "summary", "raw": "{}"},
    }

    result = asyncio.run(handle_summary(state))

    rag.search.assert_awaited_once()
    assert "어떤 정책을 요약할까요" in result["branch_content"]
    assert result["branch_policies"] == []


def test_branch_summary_recent_single_policy_runs_policy_summary(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    graph = MagicMock()
    graph.run = AsyncMock(return_value={"summary": "최근 정책 요약입니다."})
    monkeypatch.setattr(PolicyRepository, "find_policy_detail", AsyncMock(return_value=_policy()))
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    state = {
        "user_id": 7,
        "user_content": "요약해줘",
        "history": [],
        "supervisor_decision": {"intent": "summary", "raw": "{}"},
        "slot": {
            "recent_policies": [
                {"slug": "WLF1", "policy_name": "임신·출산 진료비", "last_action": "RECOMMENDED"}
            ]
        },
    }

    result = asyncio.run(handle_summary(state))

    graph.run.assert_awaited_once()
    assert result["branch_content"] == "최근 정책 요약입니다."
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_summary_recent_multiple_policies_asks_target(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    state = {
        "user_id": 7,
        "user_content": "요약해줘",
        "history": [],
        "supervisor_decision": {"intent": "summary", "raw": "{}"},
        "slot": {
            "recent_policies": [
                {"slug": "WLF1", "policy_name": "A 정책", "last_action": "RECOMMENDED"},
                {"slug": "WLF2", "policy_name": "B 정책", "last_action": "RECOMMENDED"},
            ]
        },
    }

    result = asyncio.run(handle_summary(state))

    assert "어떤 정책을 요약할까요" in result["branch_content"]
    assert result["branch_policies"] == []

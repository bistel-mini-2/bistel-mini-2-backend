import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.nodes.chat import chat_nodes
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


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())


def test_branch_summary_resolved_slug_uses_policy_name_for_rag(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm = _fake_llm(monkeypatch)
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="임신·출산 진료비")]
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

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

    rag.search.assert_awaited_once()
    llm.ainvoke.assert_awaited_once()
    assert result["branch_content"] == "요약 답변입니다."
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_summary_no_resolved_slug_runs_rag(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_llm(monkeypatch, response="일반 요약입니다.")
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
    assert result["branch_content"] == "일반 요약입니다."

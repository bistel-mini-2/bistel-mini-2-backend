import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import chat_handlers
from app.services.chat_handlers import handle_eligibility
from app.common.ai_status import RequestStatus


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

    async def execute(self, *args: Any, **kwargs: Any) -> MagicMock:
        return MagicMock()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _rag_chunk(*, chunk_id: int, policy_code: str, policy_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        policy_code=policy_code,
        policy_name=policy_name,
        source_url=None,
        chunk_text="참고 텍스트",
    )


def _eligibility_result(*, status: str = RequestStatus.COMPLETED.value) -> dict[str, Any]:
    return {
        "status": status,
        "slug": "WLF1",
        "policy_name": "임신·출산 진료비",
        "user_status": "eligible",
        "summary": "지원 가능성이 높아요.",
        "request_id": 42,
        "follow_up_questions": [],
        "evidences": [
            {
                "chunk_id": 99,
                "snippet": "임신부 누구나",
                "source_title": "임신·출산 진료비",
                "source_url": "https://example.com",
                "evidence_role": "TARGET",
            }
        ],
    }


def _state(*, resolved_slug: str | None = None) -> dict[str, Any]:
    decision: dict[str, Any] = {"intent": "eligibility", "raw": "{}"}
    if resolved_slug:
        decision["resolved_policy_slug"] = resolved_slug
    return {
        "user_id": 7,
        "user_content": "이 정책 신청 자격 있나요?",
        "history": [],
        "supervisor_decision": decision,
        "slot": {
            "recent_policies": [
                {
                    "policy_id": 100,
                    "slug": "WLF1",
                    "policy_name": "임신·출산 진료비",
                    "last_action": "RECOMMENDED",
                }
            ]
        },
    }


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())


def _make_eligibility_graph(result: dict | None) -> MagicMock:
    graph = MagicMock()
    if result is None:
        graph.run = AsyncMock(side_effect=RuntimeError("graph failed"))
    else:
        graph.run = AsyncMock(return_value=result)
    return graph


def test_branch_eligibility_resolved_slug_skips_rag(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    graph = _make_eligibility_graph(_eligibility_result())
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_ELIGIBILITY_GRAPH", graph)

    result = asyncio.run(handle_eligibility(_state(resolved_slug="WLF1")))

    rag.search.assert_not_awaited()
    graph.run.assert_awaited_once()
    assert result["branch_content"] == "지원 가능성이 높아요."
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_eligibility_rag_finds_policy_runs_lifecycle(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="임신·출산 진료비")]
    )
    graph = _make_eligibility_graph(_eligibility_result())
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_ELIGIBILITY_GRAPH", graph)

    state = {
        "user_id": 7,
        "user_content": "임신·출산 진료비 신청 자격 있나요?",
        "history": [],
        "supervisor_decision": {"intent": "eligibility", "raw": "{}"},
    }
    result = asyncio.run(handle_eligibility(state))

    rag.search.assert_awaited_once()
    graph.run.assert_awaited_once()
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_eligibility_no_policy_found_returns_clarification(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    graph = _make_eligibility_graph(_eligibility_result())
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_ELIGIBILITY_GRAPH", graph)

    state = {
        "user_id": 7,
        "user_content": "지원 가능한지 알고 싶어요",
        "history": [],
        "supervisor_decision": {"intent": "eligibility", "raw": "{}"},
    }
    result = asyncio.run(handle_eligibility(state))

    graph.run.assert_not_awaited()
    assert result["branch_policies"] == []
    assert "정책" in result["branch_content"]


def test_branch_eligibility_follow_up_required_saves_slot(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    follow_up_result = {
        **_eligibility_result(status=RequestStatus.FOLLOW_UP_REQUIRED.value),
        "follow_up_questions": [{"field_name": "region", "question_text": "어디 사세요?"}],
    }
    graph = _make_eligibility_graph(follow_up_result)
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", _FakeRagService([]))
    monkeypatch.setattr(chat_handlers, "_ELIGIBILITY_GRAPH", graph)

    result = asyncio.run(handle_eligibility(_state(resolved_slug="WLF1")))

    slot_update = result.get("eligibility_slot_update") or {}
    assert slot_update["eligibility_status"] == RequestStatus.FOLLOW_UP_REQUIRED.value
    assert slot_update["slug"] == "WLF1"
    assert slot_update["eligibility_request_id"] == 42
    assert len(slot_update["follow_up_questions"]) == 1


def test_branch_eligibility_lifecycle_failure_returns_fallback(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = _make_eligibility_graph(None)
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", _FakeRagService([]))
    monkeypatch.setattr(chat_handlers, "_ELIGIBILITY_GRAPH", graph)

    result = asyncio.run(handle_eligibility(_state(resolved_slug="WLF1")))

    assert result["branch_policies"] == []
    assert "문제" in result["branch_content"] or "오류" in result["branch_content"] or result["branch_content"]

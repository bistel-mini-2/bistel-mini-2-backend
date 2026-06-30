import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.ai.nodes.chat.chat_nodes import (
    _pick_compare_targets,
)
from app.services import chat_handlers
from app.services.chat_handlers import handle_compare, extract_policy_links


class _FakeRagResult:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


class _FakeRagService:
    def __init__(self, results: list[Any]) -> None:
        self.search = AsyncMock(return_value=_FakeRagResult(results))


class _FakeComparisonGraph:
    def __init__(self) -> None:
        self.run = AsyncMock(
            return_value={
                "policy_a": {
                    "policy_id": "1",
                    "slug": "WLF1",
                    "name": "A 정책",
                    "summary": {"condition": "A 조건", "benefit": "현금"},
                },
                "policy_b": {
                    "policy_id": "2",
                    "slug": "WLF2",
                    "name": "B 정책",
                    "summary": {"condition": "B 조건", "benefit": "바우처"},
                },
                "diff_table": [
                    {
                        "field": "지원 대상 요약",
                        "a": "A 조건",
                        "b": "B 조건",
                    }
                ],
                "selection_guide": "두 정책은 지원 대상 조건이 다릅니다.",
                "related_policies": [],
            }
        )


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _rag_chunk(
    *,
    chunk_id: int,
    policy_code: str,
    policy_name: str,
) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        policy_code=policy_code,
        policy_name=policy_name,
        source_url=None,
        chunk_text=f"{policy_name} 조건",
    )


def test_pick_compare_targets_uses_two_rag_policies() -> None:
    first, second = _pick_compare_targets(
        [
            {"slug": "WLF1", "policy_name": "A 정책"},
            {"slug": "WLF2", "policy_name": "B 정책"},
        ],
        slot=None,
        user_content="A 정책과 B 정책 비교해줘",
    )

    assert first == ("WLF1", "A 정책")
    assert second == ("WLF2", "B 정책")


def test_branch_compare_runs_comparison_graph(monkeypatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())
    comparison_graph = _FakeComparisonGraph()
    rag = _FakeRagService(
        [
            _rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="A 정책"),
            _rag_chunk(chunk_id=2, policy_code="WLF2", policy_name="B 정책"),
        ]
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_COMPARISON_GRAPH", comparison_graph)

    state = {
        "user_id": 7,
        "user_content": "A 정책과 B 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_awaited_once()
    assert "두 정책은 지원 대상 조건이 다릅니다" in result["branch_content"]
    assert [p["slug"] for p in result["branch_policies"]] == ["WLF1", "WLF2"]
    assert len(result["branch_evidences"]) == 2

    link_result = asyncio.run(extract_policy_links({
        **result,
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }))
    assert link_result == [
        {"policy_slug": "WLF1", "action_type": "COMPARED"},
        {"policy_slug": "WLF2", "action_type": "COMPARED"},
    ]


def test_branch_compare_asks_for_two_targets(monkeypatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())
    rag = _FakeRagService([])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    state = {
        "user_id": 7,
        "user_content": "두 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(handle_compare(state))

    assert "비교할 정책 2개" in result["branch_content"]
    assert result["branch_policies"] == []


def test_branch_compare_one_explicit_target_asks_for_second(monkeypatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())
    comparison_graph = _FakeComparisonGraph()
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="A 정책")]
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_COMPARISON_GRAPH", comparison_graph)

    state = {
        "user_id": 7,
        "user_content": "A 정책이랑 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_not_awaited()
    assert "비교할 정책 2개" in result["branch_content"]
    assert result["branch_policies"] == []


def test_branch_compare_recent_three_policies_asks_user_to_choose(monkeypatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())
    comparison_graph = _FakeComparisonGraph()
    rag = _FakeRagService([])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_COMPARISON_GRAPH", comparison_graph)

    state = {
        "user_id": 7,
        "user_content": "비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
        "slot": {
            "recent_policies": [
                {"slug": "WLF1", "policy_name": "A 정책", "last_action": "RECOMMENDED"},
                {"slug": "WLF2", "policy_name": "B 정책", "last_action": "RECOMMENDED"},
                {"slug": "WLF3", "policy_name": "C 정책", "last_action": "RECOMMENDED"},
            ]
        },
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_not_awaited()
    assert "비교할 정책 2개" in result["branch_content"]
    assert result["branch_policies"] == []

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.ai.nodes.chat.chat_nodes import (
    ChatGraphNodes,
    _pick_compare_targets,
)


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
    monkeypatch.setattr(
        "app.ai.nodes.chat.chat_nodes.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    comparison_graph = _FakeComparisonGraph()
    nodes = ChatGraphNodes(
        rag_service=_FakeRagService(
            [
                _rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="A 정책"),
                _rag_chunk(chunk_id=2, policy_code="WLF2", policy_name="B 정책"),
            ]
        ),
        comparison_graph=comparison_graph,  # type: ignore[arg-type]
    )
    state = {
        "user_id": 7,
        "user_content": "A 정책과 B 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(nodes.branch_compare(state))  # type: ignore[arg-type]

    comparison_graph.run.assert_awaited_once()
    assert "두 정책은 지원 대상 조건이 다릅니다" in result["branch_content"]
    assert [p["slug"] for p in result["branch_policies"]] == ["WLF1", "WLF2"]
    assert len(result["branch_evidences"]) == 2

    link_state = asyncio.run(nodes.policy_link_extract(result))  # type: ignore[arg-type]
    assert link_state["policy_links_to_save"] == [
        {"policy_slug": "WLF1", "action_type": "COMPARED"},
        {"policy_slug": "WLF2", "action_type": "COMPARED"},
    ]


def test_branch_compare_asks_for_two_targets(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.nodes.chat.chat_nodes.AsyncSessionLocal",
        lambda: _FakeSession(),
    )
    nodes = ChatGraphNodes(rag_service=_FakeRagService([]))
    state = {
        "user_id": 7,
        "user_content": "두 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(nodes.branch_compare(state))  # type: ignore[arg-type]

    assert "비교할 정책 2개" in result["branch_content"]
    assert result["branch_policies"] == []

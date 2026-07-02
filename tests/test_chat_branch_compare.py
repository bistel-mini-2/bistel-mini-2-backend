import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.nodes.chat.chat_nodes import _pick_compare_targets
from app.services import chat_handlers
from app.services.chat.ai import _graph_clients, _lifecycle_runners, _policy_resolver
from app.services.chat.ai._policy_resolver import (
    _extract_compare_policy_name_parts,
    _policy_name_match_score,
)
from app.services.chat_handlers import extract_policy_links, handle_compare


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


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    class _Mappings:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def all(self) -> list[dict[str, Any]]:
            return self._rows

    def mappings(self) -> "_FakeResult._Mappings":
        return _FakeResult._Mappings(self._rows)


class _FakeSession:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeResult:
        return _FakeResult(self._rows)


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


@pytest.fixture
def patched_clarification(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value="비교할 정책 2개를 알려주세요.")
    monkeypatch.setattr(chat_handlers, "_generate_clarification_answer", mock)
    return mock


@pytest.fixture
def no_llm_extraction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        _policy_resolver, "_llm_extract_compare_policy_names", AsyncMock(return_value=[])
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


def test_pick_compare_targets_uses_shortened_policy_names() -> None:
    first, second = _pick_compare_targets(
        [
            {"slug": "WLF1", "policy_name": "농식품바우처 사업"},
            {"slug": "WLF2", "policy_name": "건강보험 임신·출산 진료비 지원"},
        ],
        slot=None,
        user_content="농식품바우처와 건강보험 임신출산 진료비를 비교해줘",
    )

    assert first == ("WLF1", "농식품바우처 사업")
    assert second == ("WLF2", "건강보험 임신·출산 진료비 지원")


def test_extract_compare_policy_name_parts_from_user_input() -> None:
    assert _extract_compare_policy_name_parts(
        "농식품바우처와 건강보험 임신출산 진료비를 비교해줘"
    ) == ["농식품바우처", "건강보험 임신출산 진료비"]


def test_policy_name_match_score_allows_shortened_official_name() -> None:
    assert (
        _policy_name_match_score(
            "건강보험 임신출산 진료비",
            "건강보험 임신·출산 진료비 지원",
        )
        > 0
    )


def test_branch_compare_runs_comparison_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    comparison_graph = _FakeComparisonGraph()
    monkeypatch.setattr(_graph_clients, "_COMPARISON_GRAPH", comparison_graph)
    monkeypatch.setattr(_lifecycle_runners, "AsyncSessionLocal", lambda: _FakeSession())

    state = {
        "user_id": 7,
        "user_content": "A 정책과 B 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
        "slot": {
            "recent_policies": [
                {"slug": "WLF1", "policy_name": "A 정책"},
                {"slug": "WLF2", "policy_name": "B 정책"},
            ]
        },
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_awaited_once()
    assert "두 정책은 지원 대상 조건이 다릅니다" in result["branch_content"]
    assert "주요 차이" not in result["branch_content"]
    assert "정책 비교 화면" not in result["branch_content"]
    assert [p["slug"] for p in result["branch_policies"]] == ["WLF1", "WLF2"]

    link_result = asyncio.run(extract_policy_links({
        **result,
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }))
    assert link_result == [
        {"policy_slug": "WLF1", "action_type": "COMPARED"},
        {"policy_slug": "WLF2", "action_type": "COMPARED"},
    ]


def test_branch_compare_uses_direct_policy_name_lookup_when_rag_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    comparison_graph = _FakeComparisonGraph()
    monkeypatch.setattr(_graph_clients, "_RAG_SERVICE", _FakeRagService([]))
    monkeypatch.setattr(_graph_clients, "_COMPARISON_GRAPH", comparison_graph)
    monkeypatch.setattr(
        _policy_resolver,
        "AsyncSessionLocal",
        lambda: _FakeSession(
            [
                {"slug": "WLF1", "policy_name": "농식품바우처 사업"},
                {"slug": "WLF2", "policy_name": "건강보험 임신·출산 진료비 지원"},
            ]
        ),
    )
    monkeypatch.setattr(_lifecycle_runners, "AsyncSessionLocal", lambda: _FakeSession())

    state = {
        "user_id": 7,
        "user_content": "농식품바우처와 건강보험 임신출산 진료비를 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_awaited_once()
    assert "비교할 정책 2개" not in result["branch_content"]
    assert "두 정책은 지원 대상 조건이 다릅니다" in result["branch_content"]


def test_branch_compare_asks_for_two_targets(
    monkeypatch: pytest.MonkeyPatch,
    patched_clarification: AsyncMock,
    no_llm_extraction: None,
) -> None:
    monkeypatch.setattr(_graph_clients, "_RAG_SERVICE", _FakeRagService([]))
    monkeypatch.setattr(_policy_resolver, "AsyncSessionLocal", lambda: _FakeSession())

    state = {
        "user_id": 7,
        "user_content": "두 정책 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
    }

    result = asyncio.run(handle_compare(state))

    assert result["branch_content"] == "비교할 정책 2개를 알려주세요."
    assert result["branch_policies"] == []


def test_branch_compare_one_explicit_target_asks_for_second(
    monkeypatch: pytest.MonkeyPatch,
    patched_clarification: AsyncMock,
    no_llm_extraction: None,
) -> None:
    comparison_graph = _FakeComparisonGraph()
    monkeypatch.setattr(
        _graph_clients,
        "_RAG_SERVICE",
        _FakeRagService([_rag_chunk(chunk_id=1, policy_code="WLF1", policy_name="A 정책")]),
    )
    monkeypatch.setattr(_graph_clients, "_COMPARISON_GRAPH", comparison_graph)
    monkeypatch.setattr(_policy_resolver, "AsyncSessionLocal", lambda: _FakeSession())

    state = {
        "user_id": 7,
        "user_content": "A 정책이랑 비교해줘",
        "history": [],
        "supervisor_decision": {"intent": "compare", "raw": "{}"},
        "slot": {
            "recent_policies": [
                {"slug": "WLF1", "policy_name": "A 정책"},
            ]
        },
    }

    result = asyncio.run(handle_compare(state))

    comparison_graph.run.assert_not_awaited()
    assert result["branch_content"] == "비교할 정책 2개를 알려주세요."
    assert result["branch_policies"] == []


def test_branch_compare_recent_three_policies_asks_user_to_choose(
    monkeypatch: pytest.MonkeyPatch,
    patched_clarification: AsyncMock,
    no_llm_extraction: None,
) -> None:
    comparison_graph = _FakeComparisonGraph()
    monkeypatch.setattr(_graph_clients, "_RAG_SERVICE", _FakeRagService([]))
    monkeypatch.setattr(_graph_clients, "_COMPARISON_GRAPH", comparison_graph)
    monkeypatch.setattr(_policy_resolver, "AsyncSessionLocal", lambda: _FakeSession())

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
    assert result["branch_policies"] == []
    # 3개 후보 → 구조화된 정책 선택 응답 (policy_selection)
    candidates = result.get("branch_policy_candidates") or []
    assert len(candidates) == 3
    assert any(c["slug"] == "WLF1" for c in candidates)
    assert result["pending"]["kind"] == "clarification"
    assert result["pending"]["intent"] == "compare"
    assert "A 정책" in result["branch_content"]
    assert "B 정책" in result["branch_content"]

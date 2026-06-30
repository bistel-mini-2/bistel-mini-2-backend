import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import chat_handlers
from app.services.chat_handlers import (
    handle_policy_summary,
    build_assistant_payload,
    extract_evidences,
    extract_policy_links,
)
from app.repositories.policy_repository import PolicyRepository
from app.schemas.ai_contract import EvidenceChunk


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


def _chunk() -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=11,
        document_id=10,
        policy_id=100,
        policy_code="WLF1",
        policy_name="Birth Support",
        section=None,
        source_type="POLICY",
        source_url="https://example.com/11",
        chunk_text="Target and benefit source text",
        distance=0.1,
    )


def _policy() -> dict[str, Any]:
    return {
        "policy_id": 100,
        "slug": "WLF1",
        "name": "Birth Support",
        "target_description": "Pregnant users",
        "benefit_description": "Medical expense voucher",
        "application_method": "Apply online",
    }


def _policy_with_condition_profile() -> dict[str, Any]:
    return {
        **_policy(),
        "target_description": "Legacy policy detail target",
        "condition_profile_target_summary": "Condition profile target summary",
        "condition_profile_source_text": (
            "Raw selection criteria: child under 2 and low income household"
        ),
        "condition_profile_json": {
            "condition_tree": {
                "operator": "AND",
                "conditions": [
                    {
                        "field": "child_age",
                        "operator": "LT",
                        "value": 2,
                        "source_text": "child under 2",
                    }
                ],
            },
            "unsupported_conditions": [
                {
                    "source_text": "manual check source",
                    "reason": "SERVICE_FIELD_NOT_SUPPORTED",
                }
            ],
        },
    }


def _state() -> dict[str, Any]:
    return {
        "user_id": 7,
        "user_content": "Birth Support가 뭐야?",
        "history": [],
        "supervisor_decision": {"intent": "policy_summary", "raw": "{}"},
    }


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_handlers, "AsyncSessionLocal", lambda: _FakeSession())


def test_branch_policy_summary_runs_graph_and_exposes_summary_payload(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value=_policy()),
    )
    graph = MagicMock()
    graph.run = AsyncMock(
        return_value={
            "easy_summary": "Easy summary",
            "key_points": [{"label": "benefit", "content": "Voucher support"}],
            "evidence_chunks": [
                EvidenceChunk(
                    chunk_id=55,
                    policy_id=100,
                    snippet="Evidence snippet",
                    source_title="Birth Support",
                    source_url="https://example.com/source",
                    evidence_role="SUMMARY",
                )
            ],
        }
    )
    rag = _FakeRagService([_chunk()])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    branch_state = asyncio.run(handle_policy_summary(_state()))
    payload_state = asyncio.run(build_assistant_payload(branch_state))
    evidences_list = asyncio.run(extract_evidences(branch_state))
    link_result = asyncio.run(extract_policy_links(branch_state))

    graph.run.assert_awaited_once_with(_policy())
    payload = payload_state["assistant_payload"]
    assert payload["content"] == "Easy summary"
    assert payload["easy_summary"] == "Easy summary"
    assert payload["key_points"] == [
        {"label": "benefit", "content": "Voucher support"}
    ]
    assert payload["actions"] == ["chat"]
    assert payload["evidences"][0]["chunk_id"] == 55
    assert evidences_list == [
        {
            "chunk_id": 55,
            "snippet": "Evidence snippet",
            "evidence_role": "SUMMARY",
        }
    ]
    assert link_result == []


def test_branch_policy_summary_uses_resolved_slot_without_rag(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    find_detail = AsyncMock(return_value=_policy())
    monkeypatch.setattr(PolicyRepository, "find_policy_detail", find_detail)
    graph = MagicMock()
    graph.run = AsyncMock(return_value={"summary": "Slot summary"})
    rag = _FakeRagService([_chunk()])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    result = asyncio.run(
        handle_policy_summary(
            {
                **_state(),
                "supervisor_decision": {
                    "intent": "policy_summary",
                    "raw": "{}",
                    "resolved_policy_slug": "WLF1",
                },
                "slot": {
                    "recent_policies": [
                        {
                            "policy_id": 100,
                            "slug": "WLF1",
                            "policy_name": "Birth Support",
                            "last_action": "RECOMMENDED",
                        }
                    ]
                },
            }
        )
    )

    rag.search.assert_not_awaited()
    find_detail.assert_awaited_once()
    assert result["branch_content"] == "Slot summary"


def test_branch_policy_summary_fallback_key_points_prefer_condition_profile(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value=_policy_with_condition_profile()),
    )
    graph = MagicMock()
    graph.run = AsyncMock(return_value={"summary": "Profile summary"})
    rag = _FakeRagService([_chunk()])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    branch_state = asyncio.run(handle_policy_summary(_state()))
    payload_state = asyncio.run(build_assistant_payload(branch_state))

    payload = payload_state["assistant_payload"]
    assert payload["key_points"][0] == {
        "label": "target",
        "content": "Condition profile target summary",
    }
    assert payload["key_points"][1]["label"] == "benefit"
    assert "Legacy policy detail target" not in payload["key_points"][0]["content"]


def test_branch_policy_summary_without_target_asks_policy_name(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = MagicMock()
    graph.run = AsyncMock(return_value={"summary": "Should not run"})
    rag = _FakeRagService([_chunk()])
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers, "_POLICY_SUMMARY_GRAPH", graph)

    result = asyncio.run(handle_policy_summary({
        **_state(),
        "user_content": "정책을 핵심 위주로 쉽게 요약해줘",
    }))

    graph.run.assert_not_awaited()
    assert "어떤 정책을 요약할까요" in result["branch_content"]
    assert result["branch_policies"] == []

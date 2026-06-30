"""
clarification 이어받기 관련 시나리오 검증.

수정된 동작:
1. classify_intent: pending.kind == "clarification" 이면 LLM 분류 무시하고 강제 이어받기
2. handle_policy_summary / handle_summary: 정책 못 찾으면 pending clarification 저장
3. build_assistant_payload: eligibility_result 필드 포함
4. _run_eligibility_branch: branch_eligibility_result 반환
"""
import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import chat_handlers as chat_handlers_module
from app.services.chat_handlers import (
    build_assistant_payload,
    classify_intent,
    handle_policy_summary,
    handle_summary,
)


# ── 공통 픽스처 ──────────────────────────────────────────────────────────────


class _FakeRagResult:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


class _FakeRagService:
    def __init__(self, results: list[Any] | None = None) -> None:
        self.search = AsyncMock(return_value=_FakeRagResult(results or []))


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_handlers_module, "AsyncSessionLocal", lambda: _FakeSession())


def _make_llm_mock(monkeypatch: pytest.MonkeyPatch, intent: str) -> None:
    """classify_intent 내부 LLM을 지정 intent로 고정."""
    from app.ai.nodes.chat.chat_nodes import _IntentDecision

    structured = AsyncMock()
    structured.ainvoke = AsyncMock(return_value=_IntentDecision(intent=intent))
    base = MagicMock()
    base.with_structured_output = MagicMock(return_value=structured)
    monkeypatch.setattr(chat_handlers_module, "_llm", lambda: base)


# ── 1. clarification pending → LLM 분류 무시하고 강제 이어받기 ───────────────


def test_clarification_pending_overrides_llm_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM이 recommend를 반환해도 clarification pending이면 policy_summary로 강제 override."""
    _make_llm_mock(monkeypatch, "recommend")

    out = asyncio.run(classify_intent(
        user_id=1,
        user_content="부모급여요",
        history=[],
        slot={
            "pending": {"intent": "policy_summary", "kind": "clarification"},
        },
        recent_assistant_policy=None,
    ))

    assert out["supervisor_decision"]["intent"] == "policy_summary"
    # slot_request / awaiting 없어야 함 → pending_active = False
    assert not out.get("awaiting_slots")
    assert out.get("profile_confirm") is None


def test_clarification_pending_overrides_unclear(monkeypatch: pytest.MonkeyPatch) -> None:
    """LLM이 unclear를 반환해도 clarification pending이면 summary로 강제 override."""
    _make_llm_mock(monkeypatch, "unclear")

    out = asyncio.run(classify_intent(
        user_id=1,
        user_content="그거요",
        history=[],
        slot={
            "pending": {"intent": "summary", "kind": "clarification"},
        },
        recent_assistant_policy=None,
    ))

    assert out["supervisor_decision"]["intent"] == "summary"


def test_no_clarification_pending_llm_intent_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """pending 없으면 LLM 분류 결과를 그대로 사용 (기존 동작 회귀 방지)."""
    _make_llm_mock(monkeypatch, "recommend")

    out = asyncio.run(classify_intent(
        user_id=1,
        user_content="추천해줘",
        history=[],
        slot={},
        recent_assistant_policy=None,
    ))

    assert out["supervisor_decision"]["intent"] == "recommend"


def test_confirm_pending_not_affected_by_clarification_logic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """confirm pending은 여전히 2-A 블록에서 처리 (clarification 블록에 걸리지 않아야 함)."""
    _make_llm_mock(monkeypatch, "unclear")

    out = asyncio.run(classify_intent(
        user_id=1,
        user_content="네",
        history=[],
        slot={
            "pending": {"intent": "recommend", "kind": "confirm"},
        },
        recent_assistant_policy=None,
    ))

    # confirm 블록에서 처리되어 recommend intent가 되어야 함
    assert out["supervisor_decision"]["intent"] == "recommend"


# ── 2. handle_policy_summary: 정책 못 찾으면 clarification pending 저장 ───────


def test_handle_policy_summary_stores_clarification_pending_when_no_policy(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정책명 명시 없고 슬롯도 없으면 clarification pending을 반환해야 한다."""
    graph = MagicMock()
    graph.run = AsyncMock(return_value={})
    rag = _FakeRagService([])  # RAG 결과 없음
    monkeypatch.setattr(chat_handlers_module, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers_module, "_POLICY_SUMMARY_GRAPH", graph)

    result = asyncio.run(handle_policy_summary({
        "user_id": 1,
        "user_content": "정책을 요약해줘",
        "history": [],
        "slot": {},
        "supervisor_decision": {"intent": "policy_summary", "raw": "{}"},
    }))

    graph.run.assert_not_awaited()
    assert result.get("pending") == {"intent": "policy_summary", "kind": "clarification"}
    assert result["branch_policies"] == []


def test_handle_summary_stores_clarification_pending_when_no_policy(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handle_summary도 동일하게 clarification pending을 반환해야 한다."""
    graph = MagicMock()
    graph.run = AsyncMock(return_value={})
    rag = _FakeRagService([])
    monkeypatch.setattr(chat_handlers_module, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers_module, "_POLICY_SUMMARY_GRAPH", graph)

    result = asyncio.run(handle_summary({
        "user_id": 1,
        "user_content": "내용 요약해줘",
        "history": [],
        "slot": {},
        "supervisor_decision": {"intent": "summary", "raw": "{}"},
    }))

    graph.run.assert_not_awaited()
    assert result.get("pending") == {"intent": "summary", "kind": "clarification"}


def test_handle_policy_summary_clears_pending_when_policy_found(
    patched_session: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정책을 찾으면 pending 키가 없어야 한다 (상태 클리어)."""
    from app.repositories.policy_repository import PolicyRepository
    from app.schemas.ai_contract import EvidenceChunk

    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value={
            "policy_id": 100, "slug": "WLF1", "name": "부모급여",
            "target_description": "만 2세 미만",
            "benefit_description": "월 100만원",
            "application_method": "온라인 신청",
        }),
    )
    graph = MagicMock()
    graph.run = AsyncMock(return_value={
        "easy_summary": "부모급여 요약",
        "key_points": [],
        "evidence_chunks": [],
    })

    import types
    chunk = types.SimpleNamespace(
        chunk_id=1, document_id=1, policy_id=100,
        policy_code="WLF1", policy_name="부모급여",
        section=None, source_type="POLICY",
        source_url="https://example.com", chunk_text="부모급여 내용", distance=0.1,
    )
    rag = _FakeRagService([chunk])
    monkeypatch.setattr(chat_handlers_module, "_RAG_SERVICE", rag)
    monkeypatch.setattr(chat_handlers_module, "_POLICY_SUMMARY_GRAPH", graph)

    result = asyncio.run(handle_policy_summary({
        "user_id": 1,
        "user_content": "부모급여",
        "history": [],
        "slot": {"pending": {"intent": "policy_summary", "kind": "clarification"}},
        "supervisor_decision": {"intent": "policy_summary", "raw": "{}"},
    }))

    # 정책 찾았으면 pending 반환 없음 → _build_next_slot이 pending=None 저장
    assert "pending" not in result
    assert result.get("branch_content")


# ── 3. build_assistant_payload: eligibility_result 포함 ──────────────────────


def test_build_assistant_payload_includes_eligibility_result() -> None:
    """branch_eligibility_result가 state에 있으면 payload에 eligibility_result로 포함."""
    eligibility_data = {
        "status": "COMPLETED",
        "user_status": "ELIGIBLE",
        "assessment_status": None,
        "follow_up_questions": [],
        "summary": "조건 충족",
        "request_id": "req-123",
        "criteria": [],
    }
    state: dict[str, Any] = {
        "user_id": 1,
        "user_content": "나 받을 수 있어?",
        "history": [],
        "branch_content": "지원 가능합니다.",
        "branch_user_status": "ELIGIBLE",
        "branch_policies": [],
        "branch_evidences": [],
        "branch_eligibility_result": eligibility_data,
        "supervisor_decision": {"intent": "eligibility", "raw": "{}"},
    }

    result = asyncio.run(build_assistant_payload(state))
    payload = result["assistant_payload"]

    assert payload["eligibility_result"] == eligibility_data


def test_build_assistant_payload_eligibility_result_none_when_absent() -> None:
    """branch_eligibility_result가 없으면 payload의 eligibility_result는 None."""
    state: dict[str, Any] = {
        "user_id": 1,
        "user_content": "추천해줘",
        "history": [],
        "branch_content": "추천 결과입니다.",
        "branch_policies": [],
        "branch_evidences": [],
        "supervisor_decision": {"intent": "recommend", "raw": "{}"},
    }

    result = asyncio.run(build_assistant_payload(state))
    payload = result["assistant_payload"]

    assert payload.get("eligibility_result") is None


# ── 4. _run_eligibility_branch: branch_eligibility_result 반환 ───────────────


def test_run_eligibility_branch_returns_eligibility_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_eligibility_branch가 branch_eligibility_result를 state에 담아야 한다."""
    from app.services.chat_handlers import _run_eligibility_branch

    result_json = {
        "status": "COMPLETED",
        "user_status": "ELIGIBLE",
        "assessment_status": "PASS",
        "follow_up_questions": [],
        "summary": "조건 충족",
        "request_id": "req-abc",
        "criteria": [{"name": "나이", "result": "pass"}],
    }

    async def fake_lifecycle(*args, **kwargs):
        return result_json

    monkeypatch.setattr(
        chat_handlers_module,
        "_run_eligibility_lifecycle",
        fake_lifecycle,
    )

    # _adapt_eligibility_result 실제 함수가 result_json을 처리
    state: dict[str, Any] = {
        "user_id": 1,
        "user_content": "나 받을 수 있어?",
        "history": [],
        "slot": {},
        "supervisor_decision": {"intent": "eligibility", "raw": "{}"},
    }

    out = asyncio.run(
        _run_eligibility_branch(
            state=state,
            policy_slug="WLF1",
            policy_name="아이돌봄서비스",
            evidences=[],
        )
    )

    assert "branch_eligibility_result" in out
    er = out["branch_eligibility_result"]
    assert er["status"] == "COMPLETED"
    assert er["request_id"] == "req-abc"
    assert er["criteria"] == [{"name": "나이", "result": "pass"}]

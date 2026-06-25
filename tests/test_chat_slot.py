import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.nodes.chat import chat_nodes
from app.ai.nodes.chat.chat_nodes import (
    ChatGraphNodes,
    _find_slot_policy_by_slug,
    _format_slot_context,
)
from app.repositories.chat_repository import ChatRepository
from app.repositories.policy_repository import PolicyRepository
from app.services import chat_service as chat_service_module
from app.services.chat_service import ChatService, _build_next_slot
from app.db.models.chat_session import ChatSession


# ---------------- 유틸 함수 단위 테스트 ----------------


def test_format_slot_context_empty_slot_returns_none_marker() -> None:
    assert _format_slot_context(None) == "(없음)"
    assert _format_slot_context({}) == "(없음)"
    assert _format_slot_context({"recent_policies": []}) == "(없음)"


def test_format_slot_context_includes_slug_and_action() -> None:
    slot = {
        "recent_policies": [
            {
                "policy_id": 42,
                "slug": "child-care",
                "policy_name": "아이돌봄서비스",
                "last_action": "APPLY_TARGET",
            }
        ]
    }
    out = _format_slot_context(slot)
    assert "아이돌봄서비스" in out
    assert "slug=child-care" in out
    assert "action=APPLY_TARGET" in out


def test_find_slot_policy_by_slug_returns_match() -> None:
    slot = {
        "recent_policies": [
            {"slug": "a", "policy_name": "A", "policy_id": 1, "last_action": "X"},
            {"slug": "b", "policy_name": "B", "policy_id": 2, "last_action": "Y"},
        ]
    }
    assert _find_slot_policy_by_slug(slot, "b")["policy_name"] == "B"
    assert _find_slot_policy_by_slug(slot, "z") is None
    assert _find_slot_policy_by_slug(None, "a") is None
    assert _find_slot_policy_by_slug(slot, "") is None


# ---------------- _build_next_slot ----------------


def test_build_next_slot_creates_entry_from_resolved_policy() -> None:
    slot = _build_next_slot(
        current_slot={},
        policy_links=[{"policy_slug": "WLF1", "action_type": "APPLY_TARGET"}],
        branch_policies=[{"slug": "WLF1", "policy_name": "정책1"}],
        slug_to_policy_id={"WLF1": 42},
    )
    assert slot is not None
    assert slot["recent_policies"] == [
        {
            "policy_id": 42,
            "slug": "WLF1",
            "policy_name": "정책1",
            "last_action": "APPLY_TARGET",
        }
    ]
    assert "updated_at" in slot


def test_build_next_slot_returns_none_when_no_resolvable_policy() -> None:
    # slug_to_policy_id에 매핑 없으면 슬롯에 들어갈 수 없음
    assert (
        _build_next_slot(
            current_slot={"recent_policies": []},
            policy_links=[{"policy_slug": "X", "action_type": "RECOMMENDED"}],
            branch_policies=[{"slug": "X", "policy_name": "X"}],
            slug_to_policy_id={},
        )
        is None
    )


def test_build_next_slot_prepends_new_dedups_and_caps_three() -> None:
    existing = {
        "recent_policies": [
            {"policy_id": 1, "slug": "A", "policy_name": "A", "last_action": "RECOMMENDED"},
            {"policy_id": 2, "slug": "B", "policy_name": "B", "last_action": "RECOMMENDED"},
            {"policy_id": 3, "slug": "C", "policy_name": "C", "last_action": "RECOMMENDED"},
        ]
    }
    slot = _build_next_slot(
        current_slot=existing,
        policy_links=[
            {"policy_slug": "D", "action_type": "APPLY_TARGET"},
            {"policy_slug": "A", "action_type": "COMPARED"},  # 중복: A는 기존에 있음
        ],
        branch_policies=[
            {"slug": "D", "policy_name": "D"},
            {"slug": "A", "policy_name": "A"},
        ],
        slug_to_policy_id={"D": 4, "A": 1},
    )
    assert slot is not None
    slugs = [p["slug"] for p in slot["recent_policies"]]
    # 새 항목(D, A)이 앞에 prepend, 기존 A는 dedup으로 제거, cap 3
    assert slugs == ["D", "A", "B"]
    assert len(slot["recent_policies"]) == 3
    # A의 last_action은 최신(COMPARED)으로 갱신
    a_entry = next(p for p in slot["recent_policies"] if p["slug"] == "A")
    assert a_entry["last_action"] == "COMPARED"


# ---------------- branch_apply 슬롯 분기 ----------------


class _FakeRagResult:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


class _FakeRagService:
    def __init__(self) -> None:
        self.search = AsyncMock(return_value=_FakeRagResult([]))


class _FakeEligibilityGraph:
    def __init__(self) -> None:
        self.run = AsyncMock(
            return_value={
                "request_id": "123",
                "status": "COMPLETED",
                "policy_id": "42",
                "slug": "WLF1",
                "policy_name": "아이돌봄서비스",
                "user_status": "NEEDS_CONFIRMATION",
                "summary": "조건 일부는 맞지만 추가 확인이 필요해요.",
                "evidences": [
                    {
                        "chunk_id": "9",
                        "snippet": "지원 대상 근거",
                        "source_title": "아이돌봄서비스",
                        "source_url": "https://example.test",
                        "evidence_role": "target",
                    }
                ],
            }
        )


class _FakeSession:
    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return MagicMock()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_nodes, "AsyncSessionLocal", lambda: _FakeSession())


@pytest.fixture
def patched_llm(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(
        return_value=SimpleNamespace(content="신청 방법을 안내해 드릴게요.")
    )
    monkeypatch.setattr(chat_nodes, "_llm", lambda: llm)
    return llm


def _state_with_slot(slug: str = "WLF1") -> dict[str, Any]:
    return {
        "user_id": 7,
        "user_content": "신청 기간은?",
        "history": [],
        "slot": {
            "recent_policies": [
                {
                    "policy_id": 42,
                    "slug": slug,
                    "policy_name": "아이돌봄서비스",
                    "last_action": "APPLY_TARGET",
                }
            ]
        },
        "supervisor_decision": {
            "intent": "apply",
            "raw": "{}",
            "resolved_policy_slug": slug,
        },
    }


def test_branch_apply_skips_rag_when_slot_resolved(
    monkeypatch: pytest.MonkeyPatch, patched_session: None, patched_llm: AsyncMock
) -> None:
    from app.schemas.apply_schema import ApplyPreparationResponse, ChecklistItem

    rag = _FakeRagService()
    nodes = ChatGraphNodes(rag_service=rag)

    apply_response = ApplyPreparationResponse(
        apply_id=None,
        saved=False,
        policy_id="WLF1",
        how_to_apply="복지로 온라인",
        apply_period="2026-01-01",
        contact="129",
        official_url=None,
        checklist=[ChecklistItem(id="1", label="신분증", done=False)],
        caution=None,
        progress_percent=0,
    )
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService,
        "get",
        AsyncMock(return_value=apply_response),
    )

    out = asyncio.run(nodes.branch_apply(_state_with_slot()))

    # RAG는 호출되지 않아야 함
    rag.search.assert_not_called()
    # 슬롯 정책으로 apply_card가 생성됐는지
    assert out["branch_apply_card"] is not None
    assert out["branch_policies"][0]["slug"] == "WLF1"
    # policy_id 필드는 슬롯 경로에서 None (slug 들어가던 버그 수정 확인)
    assert out["branch_policies"][0]["policy_id"] is None
    # evidence는 비어 있음 (RAG 스킵의 트레이드오프)
    assert out["branch_evidences"] == []


def test_branch_apply_uses_rag_when_no_resolved_slug(
    monkeypatch: pytest.MonkeyPatch, patched_session: None, patched_llm: AsyncMock
) -> None:
    from app.schemas.apply_schema import ApplyPreparationResponse, ChecklistItem

    rag_chunk = SimpleNamespace(
        chunk_id=1,
        document_id=10,
        policy_id=100,
        policy_code="WLF1",
        policy_name="아이돌봄서비스",
        section=None,
        source_type="POLICY",
        source_url=None,
        chunk_text="신청 안내",
        distance=0.1,
    )
    rag = _FakeRagService()
    rag.search = AsyncMock(return_value=_FakeRagResult([rag_chunk]))
    nodes = ChatGraphNodes(rag_service=rag)

    apply_response = ApplyPreparationResponse(
        apply_id=None,
        saved=False,
        policy_id="WLF1",
        how_to_apply="복지로 온라인",
        apply_period="2026-01-01",
        contact="129",
        official_url=None,
        checklist=[ChecklistItem(id="1", label="신분증", done=False)],
        caution=None,
        progress_percent=0,
    )
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService,
        "get",
        AsyncMock(return_value=apply_response),
    )

    state = {
        "user_id": 7,
        "user_content": "아이돌봄서비스 어떻게 신청해?",
        "history": [],
        "slot": {},
        "supervisor_decision": {
            "intent": "apply",
            "raw": "{}",
            "resolved_policy_slug": None,
        },
    }
    out = asyncio.run(nodes.branch_apply(state))

    # 슬롯 없으면 RAG 경로
    rag.search.assert_called_once()
    assert out["branch_policies"][0]["slug"] == "WLF1"
    assert out["branch_policies"][0]["policy_id"] is None


# ---------------- send_message에서 슬롯이 graph로 전달 + 갱신 ----------------


def _session_with_slot(slot: dict) -> ChatSession:
    s = ChatSession(chat_session_id=10, user_id=1, title=None)
    s.slot_json = slot
    return s


def test_send_message_passes_slot_to_graph_and_updates_slot(monkeypatch) -> None:
    initial_slot = {
        "recent_policies": [
            {
                "policy_id": 7,
                "slug": "OLD",
                "policy_name": "옛 정책",
                "last_action": "RECOMMENDED",
            }
        ]
    }
    session = _session_with_slot(initial_slot)

    saved_messages: list[Any] = []

    async def fake_save_message(db, message):
        message.chat_message_id = 100 + len(saved_messages)
        saved_messages.append(message)
        return message

    update_slot_mock = AsyncMock()
    monkeypatch.setattr(ChatRepository, "find_session_by_id", AsyncMock(return_value=session))
    monkeypatch.setattr(ChatRepository, "find_recent_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(ChatRepository, "find_recent_assistant_policy", AsyncMock(return_value=None))
    monkeypatch.setattr(ChatRepository, "next_sequence_no", AsyncMock(side_effect=[1, 2]))
    monkeypatch.setattr(ChatRepository, "save_message", AsyncMock(side_effect=fake_save_message))
    monkeypatch.setattr(ChatRepository, "update_last_message_at", AsyncMock())
    monkeypatch.setattr(ChatRepository, "bulk_save_message_policies", AsyncMock())
    monkeypatch.setattr(ChatRepository, "bulk_save_message_evidences", AsyncMock())
    monkeypatch.setattr(ChatRepository, "update_session_slot", update_slot_mock)
    monkeypatch.setattr(
        PolicyRepository,
        "find_ids_by_codes",
        AsyncMock(return_value={"WLF1": 42}),
    )

    run_graph_mock = AsyncMock(
        return_value={
            "assistant_payload": {
                "content": "답변",
                "user_status": None,
                "sources": [],
                "policies": [
                    {
                        "policy_id": None,
                        "slug": "WLF1",
                        "policy_name": "신정책",
                        "summary": None,
                        "tag": None,
                        "tagTone": None,
                    }
                ],
                "evidences": [],
                "actions": ["apply"],
                "disclaimer": True,
                "apply_card": None,
            },
            "supervisor_decision": {
                "intent": "apply",
                "raw": "{}",
                "resolved_policy_slug": None,
            },
            "evidences_to_save": [],
            "policy_links_to_save": [
                {"policy_slug": "WLF1", "action_type": "APPLY_TARGET"}
            ],
        }
    )
    monkeypatch.setattr(chat_service_module, "_run_supervisor_graph", run_graph_mock)

    asyncio.run(
        ChatService.send_message(
            db=AsyncMock(), user_id=1, chat_session_id=10, content="신청 방법 알려줘",
        )
    )

    # graph 호출 시 slot이 전달됐는지
    run_graph_mock.assert_awaited_once()
    kwargs = run_graph_mock.await_args.kwargs
    assert kwargs["slot"] == initial_slot

    # 슬롯 갱신 호출됐는지
    update_slot_mock.assert_awaited_once()
    new_slot = update_slot_mock.await_args.args[2]
    assert new_slot["recent_policies"][0]["slug"] == "WLF1"
    assert new_slot["recent_policies"][0]["policy_id"] == 42
    assert new_slot["recent_policies"][0]["last_action"] == "APPLY_TARGET"
    # 기존 OLD 정책은 뒤로 밀려 보존
    slugs = [p["slug"] for p in new_slot["recent_policies"]]
    assert slugs == ["WLF1", "OLD"]


# ---------------- supervisor 주어 생략 + eligibility 슬롯 분기 ----------------


def test_supervisor_resolves_subject_omitted_question(monkeypatch) -> None:
    """주어 생략된 후속 질문("신청 기간은?")도 LLM이 slug 반환하면 코드가 채택."""
    from app.ai.nodes.chat.chat_nodes import _IntentDecision

    structured_llm = AsyncMock()
    structured_llm.ainvoke = AsyncMock(
        return_value=_IntentDecision(intent="apply", resolved_policy_slug="WLF1")
    )

    base_llm = MagicMock()
    base_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr(chat_nodes, "_llm", lambda: base_llm)

    nodes = ChatGraphNodes(rag_service=_FakeRagService())
    state = {
        "user_id": 7,
        "user_content": "신청 기간은?",
        "history": [
            {"role": "user", "content": "아이돌봄서비스 어떻게 신청해?"},
            {"role": "assistant", "content": "복지로에서 온라인 신청..."},
        ],
        "slot": {
            "recent_policies": [
                {
                    "policy_id": 42,
                    "slug": "WLF1",
                    "policy_name": "아이돌봄서비스",
                    "last_action": "APPLY_TARGET",
                }
            ]
        },
    }
    out = asyncio.run(nodes.supervisor(state))
    assert out["supervisor_decision"]["intent"] == "apply"
    assert out["supervisor_decision"]["resolved_policy_slug"] == "WLF1"


def test_supervisor_rejects_hallucinated_slug_not_in_slot(monkeypatch) -> None:
    """LLM이 슬롯에 없는 slug를 반환하면 코드가 None으로 무효화 (할루시네이션 방어)."""
    from app.ai.nodes.chat.chat_nodes import _IntentDecision

    structured_llm = AsyncMock()
    structured_llm.ainvoke = AsyncMock(
        return_value=_IntentDecision(intent="apply", resolved_policy_slug="GHOST")
    )

    base_llm = MagicMock()
    base_llm.with_structured_output = MagicMock(return_value=structured_llm)
    monkeypatch.setattr(chat_nodes, "_llm", lambda: base_llm)

    nodes = ChatGraphNodes(rag_service=_FakeRagService())
    state = {
        "user_id": 7,
        "user_content": "그거 신청 기간은?",
        "history": [],
        "slot": {
            "recent_policies": [
                {
                    "policy_id": 42,
                    "slug": "WLF1",
                    "policy_name": "아이돌봄서비스",
                    "last_action": "APPLY_TARGET",
                }
            ]
        },
    }
    out = asyncio.run(nodes.supervisor(state))
    assert out["supervisor_decision"]["resolved_policy_slug"] is None


def test_branch_eligibility_skips_rag_when_slot_resolved(
    monkeypatch: pytest.MonkeyPatch, patched_session: None, patched_llm: AsyncMock
) -> None:
    rag = _FakeRagService()
    eligibility_graph = _FakeEligibilityGraph()
    nodes = ChatGraphNodes(rag_service=rag, eligibility_graph=eligibility_graph)

    state = _state_with_slot("WLF1")
    state["supervisor_decision"] = {
        "intent": "eligibility",
        "raw": "{}",
        "resolved_policy_slug": "WLF1",
    }
    state["user_content"] = "나도 받을 수 있어?"

    out = asyncio.run(nodes.branch_eligibility(state))

    rag.search.assert_not_called()
    eligibility_graph.run.assert_awaited_once()
    assert eligibility_graph.run.await_args.kwargs["policy_identifier"] == "WLF1"
    assert out["branch_policies"][0]["slug"] == "WLF1"
    assert out["branch_policies"][0]["policy_id"] == "42"
    assert out["branch_user_status"] == "NEEDS_CONFIRMATION"
    assert out["branch_evidences"][0]["chunk_id"] == "9"
    assert out["branch_evidences"][0]["evidence_role"] == "TARGET"

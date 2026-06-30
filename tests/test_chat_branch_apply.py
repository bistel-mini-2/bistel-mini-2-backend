import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import status

from app.ai.nodes.chat import chat_nodes
from app.ai.nodes.chat.chat_nodes import (
    _build_apply_card,
    _format_application_period_context,
    _format_apply_card_context,
    _is_context_dependent_apply_question,
    _pick_apply_target,
    _user_mentions_policy_name,
)
from app.common.exceptions import AppException, ErrorCode
from app.schemas.apply_schema import ApplyPreparationResponse, ChecklistItem
from app.services import chat_handlers
from app.services.chat_handlers import (
    handle_apply,
    build_assistant_payload,
    extract_policy_links,
)


def _apply_response() -> ApplyPreparationResponse:
    return ApplyPreparationResponse(
        apply_id=None,
        saved=False,
        policy_id="WLF1",
        how_to_apply="복지로 온라인 신청",
        contact="129",
        official_url="https://www.bokjiro.go.kr",
        checklist=[
            ChecklistItem(id="1", label="신분증", done=False),
            ChecklistItem(id="2", label="통장 사본", done=False),
        ],
        caution="공식 사이트에서 최종 확인하세요",
        progress_percent=0,
    )


class _FakeRagResult:
    def __init__(self, results: list[Any]) -> None:
        self.results = results


def _rag_chunk(
    *,
    chunk_id: int,
    policy_code: str | None,
    policy_name: str | None,
    text: str = "참고 텍스트",
    source_url: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        document_id=10,
        policy_id=100,
        policy_code=policy_code,
        policy_name=policy_name,
        section=None,
        source_type="POLICY",
        source_url=source_url,
        chunk_text=text,
        distance=0.1,
    )


class _FakeRagService:
    def __init__(self, results: list[Any]) -> None:
        self._results = results
        self.search = AsyncMock(return_value=_FakeRagResult(results))


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


def _state() -> dict[str, Any]:
    return {
        "user_id": 7,
        "user_content": "아이돌봄서비스 어떻게 신청해?",
        "history": [],
    }


def test_pick_apply_target_returns_first_with_slug() -> None:
    policies = [
        {"slug": None, "policy_name": "X"},
        {"slug": "WLF1", "policy_name": "임신·출산 진료비"},
        {"slug": "WLF2", "policy_name": "산모·신생아"},
    ]
    slug, name = _pick_apply_target(policies)
    assert slug == "WLF1"
    assert name == "임신·출산 진료비"


def test_pick_apply_target_requires_policy_name_mention() -> None:
    policies = [
        {"slug": "WLF1", "policy_name": "임신·출산 진료비"},
        {"slug": "WLF2", "policy_name": "아이돌봄서비스"},
    ]

    slug, name = _pick_apply_target(
        policies,
        user_content="아이돌봄 서비스 어떻게 신청해?",
        require_policy_name_mention=True,
    )

    assert slug == "WLF2"
    assert name == "아이돌봄서비스"


def test_user_mentions_policy_name_ignores_spacing_and_symbols() -> None:
    assert _user_mentions_policy_name("임신 출산 진료비 신청 방법", "임신·출산 진료비")


def test_context_dependent_apply_question_detects_follow_up_phrasing() -> None:
    assert _is_context_dependent_apply_question("이거 어떻게 신청해?")
    assert _is_context_dependent_apply_question("신청 방법은?")
    assert not _is_context_dependent_apply_question("추천 정책 알려줘")


def test_pick_apply_target_returns_none_when_empty() -> None:
    assert _pick_apply_target([]) == (None, None)


def test_build_apply_card_maps_fields_and_limits_checklist() -> None:
    response = ApplyPreparationResponse(
        apply_id=None,
        saved=False,
        policy_id="WLF1",
        how_to_apply="온라인",
        contact="129",
        official_url="https://example.com",
        checklist=[
            ChecklistItem(id=str(i), label=f"항목{i}", done=False)
            for i in range(1, 10)
        ],
        caution="주의",
        progress_percent=0,
    )

    card = _build_apply_card(response, "테스트 정책")

    assert card["policy_id"] == "WLF1"
    assert card["policy_name"] == "테스트 정책"
    assert card["how_to_apply"] == "온라인"
    assert "apply_period" not in card
    assert len(card["checklist"]) == 5
    assert card["checklist"][0] == {"id": "1", "label": "항목1", "done": False}


def test_application_period_context_is_internal_only() -> None:
    context = _format_application_period_context(
        {
            "application_status": "AVAILABLE",
            "application_period_text": "상시 신청",
            "application_start_date": "2026-01-01",
            "application_end_date": None,
            "source_text": "신청기간은 별도 공지합니다.",
            "source_fields": ["application_period_text", "source_text"],
        }
    )

    assert "신청 기간 텍스트: 상시 신청" in context
    assert "조건/원문 source_text: 신청기간은 별도 공지합니다." in context
    assert "source_fields: application_period_text, source_text" in context


def test_format_apply_card_context_lists_known_fields() -> None:
    card = _build_apply_card(_apply_response(), "임신·출산 진료비")
    text = _format_apply_card_context(card)
    assert "임신·출산 진료비" in text
    assert "복지로" in text
    assert "129" in text
    assert "신분증" in text


def test_branch_apply_invokes_service_and_adapts_payload(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [
            _rag_chunk(
                chunk_id=11,
                policy_code="WLF1",
                policy_name="아이돌봄서비스",
                text="임신부 누구나 신청할 수 있습니다.",
                source_url="https://example.com/11",
            )
        ]
    )
    apply_get = AsyncMock(return_value=_apply_response())
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply(_state()))

    apply_get.assert_awaited_once()
    call_kwargs = apply_get.await_args.kwargs
    assert call_kwargs["user_id"] == 7
    assert call_kwargs["policy_slug"] == "WLF1"

    assert result["branch_content"] == "신청 방법을 안내해 드릴게요."
    assert [p["slug"] for p in result["branch_policies"]] == ["WLF1"]
    assert result["branch_evidences"][0]["chunk_id"] == 11
    apply_card = result["branch_apply_card"]
    assert apply_card["policy_id"] == "WLF1"
    assert apply_card["how_to_apply"] == "복지로 온라인 신청"
    assert apply_card["contact"] == "129"
    assert len(apply_card["checklist"]) == 2


def test_branch_apply_falls_back_when_rag_has_no_slug(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code=None, policy_name=None)]
    )
    apply_get = AsyncMock()
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply(_state()))

    apply_get.assert_not_awaited()
    assert result["branch_apply_card"] is None
    assert result["branch_policies"] == []
    assert result["branch_evidences"] == []
    assert result["branch_content"].startswith("어떤 정책의 신청 방법")


def test_branch_apply_clarifies_when_rag_policy_name_not_mentioned(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF1", policy_name="임신·출산 진료비")]
    )
    apply_get = AsyncMock()
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply(_state()))

    apply_get.assert_not_awaited()
    assert result["branch_apply_card"] is None
    assert result["branch_policies"] == []
    assert result["branch_evidences"] == []
    assert result["branch_content"].startswith("어떤 정책의 신청 방법")


def test_branch_apply_uses_recent_assistant_policy_for_contextual_follow_up(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF2", policy_name="다른 정책")]
    )
    apply_get = AsyncMock(return_value=_apply_response())
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply({
        **_state(),
        "user_content": "이거 어떻게 신청해?",
        "recent_assistant_policy": {
            "policy_id": 42,
            "slug": "WLF1",
            "policy_name": "아이돌봄서비스",
            "action_type": "RECOMMENDED",
        },
    }))

    rag.search.assert_awaited_once()
    apply_get.assert_awaited_once()
    assert apply_get.await_args.kwargs["policy_slug"] == "WLF1"
    assert result["branch_policies"][0]["slug"] == "WLF1"
    assert result["branch_apply_card"]["policy_name"] == "아이돌봄서비스"


def test_branch_apply_recent_single_policy_runs_apply_flow(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    apply_get = AsyncMock(return_value=_apply_response())
    monkeypatch.setattr(chat_nodes.ApplyPreparationService, "get", apply_get)
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply({
        **_state(),
        "user_content": "신청 준비해줘",
        "slot": {
            "recent_policies": [
                {"policy_id": 42, "slug": "WLF1", "policy_name": "아이돌봄서비스", "last_action": "RECOMMENDED"}
            ]
        },
    }))

    apply_get.assert_awaited_once()
    assert apply_get.await_args.kwargs["policy_slug"] == "WLF1"
    assert result["branch_policies"][0]["slug"] == "WLF1"


def test_branch_apply_recent_multiple_policies_asks_target(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService([])
    apply_get = AsyncMock(return_value=_apply_response())
    monkeypatch.setattr(chat_nodes.ApplyPreparationService, "get", apply_get)
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply({
        **_state(),
        "user_content": "신청 준비해줘",
        "slot": {
            "recent_policies": [
                {"policy_id": 42, "slug": "WLF1", "policy_name": "A 정책", "last_action": "RECOMMENDED"},
                {"policy_id": 43, "slug": "WLF2", "policy_name": "B 정책", "last_action": "RECOMMENDED"},
            ]
        },
    }))

    apply_get.assert_not_awaited()
    assert "어떤 정책" in result["branch_content"]
    assert result["branch_policies"] == []


def test_branch_apply_prefers_explicit_rag_policy_over_recent_assistant_policy(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF2", policy_name="산모신생아 건강관리")]
    )
    apply_get = AsyncMock(return_value=_apply_response())
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply({
        **_state(),
        "user_content": "산모신생아 건강관리 신청 방법 알려줘",
        "recent_assistant_policy": {
            "policy_id": 42,
            "slug": "WLF1",
            "policy_name": "아이돌봄서비스",
            "action_type": "RECOMMENDED",
        },
    }))

    rag.search.assert_awaited_once()
    apply_get.assert_awaited_once()
    assert apply_get.await_args.kwargs["policy_slug"] == "WLF2"
    assert result["branch_policies"][0]["slug"] == "WLF2"


def test_branch_apply_does_not_use_recent_policy_for_non_contextual_message(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF2", policy_name="산모신생아 건강관리")]
    )
    apply_get = AsyncMock()
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", apply_get
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply({
        **_state(),
        "user_content": "새로운 정책 찾아줘",
        "recent_assistant_policy": {
            "policy_id": 42,
            "slug": "WLF1",
            "policy_name": "아이돌봄서비스",
            "action_type": "RECOMMENDED",
        },
    }))

    rag.search.assert_awaited_once()
    apply_get.assert_not_awaited()
    assert result["branch_apply_card"] is None
    assert result["branch_policies"] == []


def test_branch_apply_falls_back_when_policy_not_found(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF1", policy_name="아이돌봄서비스")]
    )

    async def _raise_not_found(*args: Any, **kwargs: Any) -> Any:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.POLICY_NOT_FOUND,
            message="Policy not found",
        )

    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService, "get", AsyncMock(side_effect=_raise_not_found)
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    result = asyncio.run(handle_apply(_state()))

    assert result["branch_apply_card"] is None
    assert result["branch_policies"] == []
    assert result["branch_evidences"][0]["chunk_id"] == 11


def test_branch_apply_reraises_non_policy_not_found_app_exception(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF1", policy_name="아이돌봄서비스")]
    )

    async def _raise_unauthorized(*args: Any, **kwargs: Any) -> Any:
        raise AppException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.UNAUTHORIZED,
            message="unauthorized",
        )

    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService,
        "get",
        AsyncMock(side_effect=_raise_unauthorized),
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    with pytest.raises(AppException):
        asyncio.run(handle_apply(_state()))


def test_branch_apply_policy_link_extract_emits_apply_target(
    patched_session: None,
    patched_llm: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = _FakeRagService(
        [_rag_chunk(chunk_id=11, policy_code="WLF1", policy_name="아이돌봄서비스")]
    )
    monkeypatch.setattr(
        chat_nodes.ApplyPreparationService,
        "get",
        AsyncMock(return_value=_apply_response()),
    )
    monkeypatch.setattr(chat_handlers, "_RAG_SERVICE", rag)

    after_branch = asyncio.run(handle_apply(_state()))

    state_with_decision = {
        **after_branch,
        "supervisor_decision": {"intent": "apply", "raw": "{}"},
    }
    payload_state = asyncio.run(build_assistant_payload(state_with_decision))
    link_result = asyncio.run(extract_policy_links(state_with_decision))

    assert payload_state["assistant_payload"]["apply_card"]["policy_id"] == "WLF1"
    assert link_result == [
        {"policy_slug": "WLF1", "action_type": "APPLY_TARGET"}
    ]

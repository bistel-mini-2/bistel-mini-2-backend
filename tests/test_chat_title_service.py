import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.services import chat_title_service as chat_title_service_module
from app.services.chat_title_service import (
    FALLBACK_TITLE,
    TITLE_MAX_LENGTH,
    assign_title_if_missing,
    generate_session_title,
)


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.calls: list = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        response = MagicMock()
        response.content = self._content
        return response


class _ExplodingLLM:
    async def ainvoke(self, messages):
        raise RuntimeError("llm down")


def _patch_llm(monkeypatch, llm) -> None:
    monkeypatch.setattr(chat_title_service_module, "_llm", lambda: llm)


def test_generate_session_title_returns_sanitized_title(monkeypatch) -> None:
    _patch_llm(monkeypatch, _FakeLLM('"육아휴직 신청 방법"\n  '))

    title = asyncio.run(generate_session_title("육아휴직 어떻게 신청해?"))

    assert title == "육아휴직 신청 방법"


def test_generate_session_title_truncates_long_response(monkeypatch) -> None:
    long_response = "가" * 80
    _patch_llm(monkeypatch, _FakeLLM(long_response))

    title = asyncio.run(generate_session_title("무언가 질문"))

    assert len(title) == TITLE_MAX_LENGTH
    assert title == "가" * TITLE_MAX_LENGTH


def test_generate_session_title_returns_fallback_for_empty_response(monkeypatch) -> None:
    _patch_llm(monkeypatch, _FakeLLM("   "))

    title = asyncio.run(generate_session_title("뭐 좀 묻고 싶은데"))

    assert title == FALLBACK_TITLE


def test_generate_session_title_returns_fallback_for_empty_input(monkeypatch) -> None:
    llm = _FakeLLM("anything")
    _patch_llm(monkeypatch, llm)

    title = asyncio.run(generate_session_title("   "))

    assert title == FALLBACK_TITLE
    # 빈 입력은 LLM 호출 자체를 건너뛴다
    assert llm.calls == []


def test_generate_session_title_returns_fallback_on_llm_error(monkeypatch) -> None:
    _patch_llm(monkeypatch, _ExplodingLLM())

    title = asyncio.run(generate_session_title("질문"))

    assert title == FALLBACK_TITLE


def _patch_session_local(monkeypatch) -> AsyncMock:
    db = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    class _Ctx:
        async def __aenter__(self_inner):
            return db

        async def __aexit__(self_inner, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        chat_title_service_module, "AsyncSessionLocal", lambda: _Ctx()
    )
    return db


def test_assign_title_if_missing_updates_when_title_blank(monkeypatch) -> None:
    db = _patch_session_local(monkeypatch)
    session = ChatSession(chat_session_id=10, user_id=1, title=None)

    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=session),
    )
    update_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(ChatRepository, "update_title_if_missing", update_mock)
    monkeypatch.setattr(
        chat_title_service_module,
        "generate_session_title",
        AsyncMock(return_value="생성된 제목"),
    )

    asyncio.run(assign_title_if_missing(10, "사용자 첫 메시지"))

    update_mock.assert_awaited_once_with(db, 10, "생성된 제목")
    db.commit.assert_awaited_once()


def test_assign_title_if_missing_skips_when_title_present(monkeypatch) -> None:
    db = _patch_session_local(monkeypatch)
    session = ChatSession(chat_session_id=10, user_id=1, title="이미 있는 제목")

    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=session),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatRepository, "update_title_if_missing", update_mock)
    generate_mock = AsyncMock(return_value="생성된 제목")
    monkeypatch.setattr(
        chat_title_service_module, "generate_session_title", generate_mock,
    )

    asyncio.run(assign_title_if_missing(10, "사용자 첫 메시지"))

    generate_mock.assert_not_called()
    update_mock.assert_not_called()
    db.commit.assert_not_called()


def test_assign_title_if_missing_skips_when_session_missing(monkeypatch) -> None:
    db = _patch_session_local(monkeypatch)

    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=None),
    )
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatRepository, "update_title_if_missing", update_mock)

    asyncio.run(assign_title_if_missing(10, "사용자 첫 메시지"))

    update_mock.assert_not_called()
    db.commit.assert_not_called()


def test_assign_title_if_missing_uses_conditional_update(monkeypatch) -> None:
    db = _patch_session_local(monkeypatch)
    session = ChatSession(chat_session_id=10, user_id=1, title=None)

    monkeypatch.setattr(
        ChatRepository, "find_session_by_id", AsyncMock(return_value=session),
    )
    manual_update_mock = AsyncMock()
    conditional_update_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(ChatRepository, "update_title", manual_update_mock)
    monkeypatch.setattr(
        ChatRepository, "update_title_if_missing", conditional_update_mock,
    )
    monkeypatch.setattr(
        chat_title_service_module,
        "generate_session_title",
        AsyncMock(return_value="생성된 제목"),
    )

    asyncio.run(assign_title_if_missing(10, "사용자 첫 메시지"))

    conditional_update_mock.assert_awaited_once_with(db, 10, "생성된 제목")
    manual_update_mock.assert_not_awaited()
    db.commit.assert_awaited_once()

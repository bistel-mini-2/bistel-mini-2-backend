from collections.abc import AsyncGenerator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.chat_controller import router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.chat_schema import (
    AssistantMessage,
    AssistantMessageEvidence,
    AssistantMessagePolicy,
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionTitleUpdateResponse,
)
from app.services.chat_service import ChatService


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)

    async def fake_db() -> AsyncGenerator[object, None]:
        yield object()

    async def fake_user() -> SimpleNamespace:
        return SimpleNamespace(user_id=5, email="t@t.com", nickname="t")

    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_user
    return app


def test_create_chat_session_returns_201(monkeypatch) -> None:
    monkeypatch.setattr(
        ChatService,
        "create_session",
        AsyncMock(return_value=ChatSessionCreateResponse(
            chat_session_id="9",
            session_status="ACTIVE",
        )),
    )

    with TestClient(_build_app()) as client:
        resp = client.post("/api/v1/chat/sessions", json={"title": "테스트"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["chat_session_id"] == "9"
    assert body["data"]["session_status"] == "ACTIVE"


def test_update_chat_session_title_returns_updated_title(monkeypatch) -> None:
    update_mock = AsyncMock(return_value=ChatSessionTitleUpdateResponse(
        chat_session_id="9",
        title="수정 제목",
        updated_at=datetime(2026, 6, 23, 10, 30, 0),
    ))
    monkeypatch.setattr(ChatService, "update_session_title", update_mock)

    with TestClient(_build_app()) as client:
        resp = client.patch(
            "/api/v1/chat/sessions/9",
            json={"title": "  수정 제목  "},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["chat_session_id"] == "9"
    assert body["data"]["title"] == "수정 제목"
    update_mock.assert_awaited_once()
    assert update_mock.await_args.kwargs["chat_session_id"] == 9
    assert update_mock.await_args.kwargs["title"] == "수정 제목"


def test_update_chat_session_title_rejects_blank_title(monkeypatch) -> None:
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatService, "update_session_title", update_mock)

    with TestClient(_build_app()) as client:
        resp = client.patch("/api/v1/chat/sessions/9", json={"title": "   "})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    update_mock.assert_not_awaited()


def test_update_chat_session_title_rejects_over_max_length(monkeypatch) -> None:
    update_mock = AsyncMock()
    monkeypatch.setattr(ChatService, "update_session_title", update_mock)

    with TestClient(_build_app()) as client:
        resp = client.patch(
            "/api/v1/chat/sessions/9",
            json={"title": "가" * 256},
        )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
    update_mock.assert_not_awaited()


def test_send_chat_message_serializes_normalized_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        ChatService,
        "send_message",
        AsyncMock(return_value=ChatMessageSendResponse(
            chat_session_id="9",
            user_message_id="11",
            assistant_message=AssistantMessage(
                chat_message_id="12",
                content="답변",
                user_status=None,
                sources=[],
                policies=[
                    AssistantMessagePolicy(
                        policy_id="42",
                        slug="WLF1",
                        policy_name="정책1",
                        action_type="RECOMMENDED",
                    ),
                ],
                actions=["recommend"],
                evidences=[
                    AssistantMessageEvidence(
                        chunk_id="101",
                        snippet="근거",
                        source_title="정책1",
                        source_url="https://example.com/1",
                        evidence_role="SUMMARY",
                    ),
                ],
                disclaimer=True,
            ),
        )),
    )

    with TestClient(_build_app()) as client:
        resp = client.post(
            "/api/v1/chat/sessions/9/messages",
            json={"content": "추천해줘"},
        )

    assert resp.status_code == 201
    am = resp.json()["data"]["assistant_message"]

    # 신규 필드가 JSON으로 직렬화되는지
    assert am["policies"][0]["action_type"] == "RECOMMENDED"
    assert am["evidences"][0]["chunk_id"] == "101"
    # evidence_role lowercase 변환 (Pydantic field_validator)
    assert am["evidences"][0]["evidence_role"] == "summary"
    assert am["actions"] == ["recommend"]
    assert am["disclaimer"] is True


def test_list_chat_messages_includes_normalized_join(monkeypatch) -> None:
    monkeypatch.setattr(
        ChatService,
        "list_messages",
        AsyncMock(return_value=ChatMessageListResponse(
            chat_session_id="9",
            messages=[
                ChatMessageItem(
                    chat_message_id="11",
                    role="user",
                    message_type="TEXT",
                    content="추천해줘",
                    sequence_no=1,
                    created_at=None,
                ),
                ChatMessageItem(
                    chat_message_id="12",
                    role="assistant",
                    message_type="TEXT",
                    content="답변",
                    sequence_no=2,
                    created_at=None,
                    user_status=None,
                    sources=[],
                    policies=[
                        AssistantMessagePolicy(
                            policy_id="42",
                            slug="WLF1",
                            policy_name="정책1",
                            action_type="RECOMMENDED",
                        ),
                    ],
                    actions=["recommend"],
                    evidences=[
                        AssistantMessageEvidence(
                            chunk_id="101",
                            snippet="근거",
                            source_title="정책1",
                            source_url="https://example.com/1",
                            evidence_role="SUMMARY",
                        ),
                    ],
                    disclaimer=True,
                ),
            ],
        )),
    )

    with TestClient(_build_app()) as client:
        resp = client.get("/api/v1/chat/sessions/9/messages")

    assert resp.status_code == 200
    messages = resp.json()["data"]["messages"]
    assert len(messages) == 2

    user_msg = messages[0]
    assert user_msg["role"] == "user"
    assert user_msg["policies"] == []
    assert user_msg["evidences"] == []

    assistant_msg = messages[1]
    assert assistant_msg["policies"][0]["action_type"] == "RECOMMENDED"
    assert assistant_msg["evidences"][0]["chunk_id"] == "101"
    assert assistant_msg["evidences"][0]["evidence_role"] == "summary"
    assert assistant_msg["actions"] == ["recommend"]

import logging
from datetime import datetime

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppException, ErrorCode
from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat_schema import (
    AssistantMessage,
    AssistantMessageEvidence,
    AssistantMessagePolicy,
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
)
from app.ai.graphs.chat_supervisor_graph import (
    HISTORY_LIMIT,
    chat_supervisor_graph,
)


logger = logging.getLogger(f"{__name__}.ChatService")


class ChatService:
    @staticmethod
    async def create_session(
        db: AsyncSession, user_id: int, title: str | None
    ) -> ChatSessionCreateResponse:
        session = await ChatRepository.save_session(
            db,
            ChatSession(user_id=user_id, title=title),
        )
        return ChatSessionCreateResponse(
            chat_session_id=str(session.chat_session_id),
            session_status=session.session_status,
        )

    @staticmethod
    async def list_sessions(
        db: AsyncSession, user_id: int
    ) -> ChatSessionListResponse:
        sessions = await ChatRepository.find_sessions_by_user(db, user_id)
        return ChatSessionListResponse(
            sessions=[
                ChatSessionListItem(
                    chat_session_id=str(session.chat_session_id),
                    title=session.title,
                    session_status=session.session_status,
                    last_message_at=session.last_message_at,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                for session in sessions
            ]
        )

    @staticmethod
    async def list_messages(
        db: AsyncSession, user_id: int, chat_session_id: int
    ) -> ChatMessageListResponse:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        messages = await ChatRepository.find_messages_by_session(
            db, session.chat_session_id
        )
        return ChatMessageListResponse(
            chat_session_id=str(session.chat_session_id),
            messages=[_to_message_item(message) for message in messages],
        )

    @staticmethod
    async def send_message(
        db: AsyncSession,
        user_id: int,
        chat_session_id: int,
        content: str,
    ) -> ChatMessageSendResponse:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)

        history_models = await ChatRepository.find_recent_messages(
            db, session.chat_session_id, limit=HISTORY_LIMIT
        )
        history = [
            {"role": message.role, "content": message.content}
            for message in history_models
            if message.content
        ]

        user_sequence = await ChatRepository.next_sequence_no(
            db, session.chat_session_id
        )
        user_message = await ChatRepository.save_message(
            db,
            ChatMessage(
                chat_session_id=session.chat_session_id,
                role="user",
                message_type="TEXT",
                content=content,
                sequence_no=user_sequence,
            ),
        )

        graph_state = {
            "user_id": user_id,
            "user_content": content,
            "history": history,
        }
        try:
            graph_result = await chat_supervisor_graph.ainvoke(graph_state)
        except Exception:
            logger.exception("Chat supervisor graph failed")
            graph_result = {
                "assistant_payload": _fallback_payload(),
                "supervisor_decision": {"intent": "unclear", "raw": "graph_error"},
            }

        payload = graph_result.get("assistant_payload") or _fallback_payload()
        decision = graph_result.get("supervisor_decision") or {
            "intent": "unclear",
            "raw": "missing",
        }

        assistant_sequence = await ChatRepository.next_sequence_no(
            db, session.chat_session_id
        )
        structured_json = {
            "assistant_payload": payload,
            "_supervisor": decision,
        }
        assistant_message = await ChatRepository.save_message(
            db,
            ChatMessage(
                chat_session_id=session.chat_session_id,
                parent_message_id=user_message.chat_message_id,
                role="assistant",
                message_type="TEXT",
                content=payload.get("content"),
                structured_json=structured_json,
                sequence_no=assistant_sequence,
            ),
        )

        await ChatRepository.update_last_message_at(
            db, session.chat_session_id, datetime.utcnow()
        )

        return ChatMessageSendResponse(
            chat_session_id=str(session.chat_session_id),
            user_message_id=str(user_message.chat_message_id),
            assistant_message=AssistantMessage(
                chat_message_id=str(assistant_message.chat_message_id),
                content=payload.get("content") or "",
                user_status=payload.get("user_status"),
                sources=payload.get("sources", []),
                policies=[
                    AssistantMessagePolicy(**policy)
                    for policy in payload.get("policies", [])
                ],
                actions=payload.get("actions", []),
                evidences=[
                    AssistantMessageEvidence(**evidence)
                    for evidence in payload.get("evidences", [])
                ],
                disclaimer=payload.get("disclaimer"),
            ),
        )


def _to_message_item(message: ChatMessage) -> ChatMessageItem:
    extras = _unwrap_assistant_extras(message.structured_json)
    return ChatMessageItem(
        chat_message_id=str(message.chat_message_id),
        role=message.role,
        message_type=message.message_type,
        content=message.content,
        sequence_no=message.sequence_no,
        created_at=message.created_at,
        **extras,
    )


def _unwrap_assistant_extras(structured_json: dict | None) -> dict:
    if not structured_json or "assistant_payload" not in structured_json:
        return {}
    payload = structured_json["assistant_payload"]
    return {
        "user_status": payload.get("user_status"),
        "sources": payload.get("sources", []),
        "policies": [
            AssistantMessagePolicy(**policy)
            for policy in payload.get("policies", [])
        ],
        "actions": payload.get("actions", []),
        "evidences": [
            AssistantMessageEvidence(**evidence)
            for evidence in payload.get("evidences", [])
        ],
        "disclaimer": payload.get("disclaimer"),
    }


def _fallback_payload() -> dict:
    return {
        "content": "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
        "user_status": None,
        "sources": [],
        "policies": [],
        "evidences": [],
        "actions": [],
        "disclaimer": False,
    }


async def _get_owned_session_or_raise(
    db: AsyncSession, user_id: int, chat_session_id: int
) -> ChatSession:
    session = await ChatRepository.find_session_by_id(db, chat_session_id)
    if session is None or session.user_id != user_id:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Chat session not found",
        )
    return session

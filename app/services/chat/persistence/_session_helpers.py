import asyncio
import logging
from datetime import datetime, timezone

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.states.chat_state import HistoryMessage
from app.common.exceptions import AppException, ErrorCode
from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 5


async def get_owned_session_or_raise(
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


async def load_history(db: AsyncSession, chat_session_id: int) -> list[HistoryMessage]:
    history_models = await ChatRepository.find_recent_messages(
        db, chat_session_id, limit=HISTORY_LIMIT
    )
    return [
        {"role": message.role, "content": message.content}
        for message in history_models
        if message.content
    ]


async def save_user_message(
    db: AsyncSession, chat_session_id: int, content: str
) -> ChatMessage:
    sequence_no = await ChatRepository.next_sequence_no(db, chat_session_id)
    return await ChatRepository.save_message(
        db,
        ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            message_type="TEXT",
            content=content,
            sequence_no=sequence_no,
        ),
    )


def chat_session_id_from_source_ref(source_ref_id: str | None) -> int | None:
    if not source_ref_id or not source_ref_id.startswith("chat_session:"):
        return None
    raw_id = source_ref_id.removeprefix("chat_session:").split(";", 1)[0].strip()
    if not raw_id.isdecimal():
        return None
    return int(raw_id)


def schedule_title_generation(chat_session_id: int, user_content: str) -> None:
    from app.services.chat_title_service import assign_title_if_missing
    asyncio.create_task(assign_title_if_missing(chat_session_id, user_content))


async def is_cancelled_or_deleted(
    db: AsyncSession,
    chat_session_id: int,
    cancel_event: asyncio.Event,
) -> bool:
    if cancel_event.is_set():
        return True
    return not await ChatRepository.session_exists(db, chat_session_id)

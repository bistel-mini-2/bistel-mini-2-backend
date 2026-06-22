from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession


class ChatRepository:
    @staticmethod
    async def save_session(db: AsyncSession, session: ChatSession) -> ChatSession:
        db.add(session)
        await db.flush()
        await db.refresh(session)
        return session

    @staticmethod
    async def find_session_by_id(
        db: AsyncSession, chat_session_id: int
    ) -> ChatSession | None:
        result = await db.execute(
            select(ChatSession).where(ChatSession.chat_session_id == chat_session_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_sessions_by_user(
        db: AsyncSession, user_id: int
    ) -> list[ChatSession]:
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(
                ChatSession.last_message_at.desc().nulls_last(),
                ChatSession.created_at.desc(),
            )
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_messages_by_session(
        db: AsyncSession, chat_session_id: int
    ) -> list[ChatMessage]:
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.chat_session_id == chat_session_id)
            .order_by(ChatMessage.sequence_no.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def find_recent_messages(
        db: AsyncSession, chat_session_id: int, limit: int
    ) -> list[ChatMessage]:
        result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.chat_session_id == chat_session_id)
            .order_by(ChatMessage.sequence_no.desc())
            .limit(limit)
        )
        messages = list(result.scalars().all())
        messages.reverse()
        return messages

    @staticmethod
    async def next_sequence_no(
        db: AsyncSession, chat_session_id: int
    ) -> int:
        result = await db.execute(
            select(func.coalesce(func.max(ChatMessage.sequence_no), 0))
            .where(ChatMessage.chat_session_id == chat_session_id)
        )
        return int(result.scalar_one()) + 1

    @staticmethod
    async def save_message(db: AsyncSession, message: ChatMessage) -> ChatMessage:
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

    @staticmethod
    async def update_last_message_at(
        db: AsyncSession, chat_session_id: int, when: datetime
    ) -> None:
        await db.execute(
            update(ChatSession)
            .where(ChatSession.chat_session_id == chat_session_id)
            .values(last_message_at=when, updated_at=when)
        )

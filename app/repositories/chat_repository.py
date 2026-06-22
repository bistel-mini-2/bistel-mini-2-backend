from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_message import ChatMessage
from app.db.models.chat_message_evidence import ChatMessageEvidence
from app.db.models.chat_message_policy import ChatMessagePolicy
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

    @staticmethod
    async def bulk_save_message_policies(
        db: AsyncSession, links: list[dict]
    ) -> None:
        if not links:
            return
        db.add_all([ChatMessagePolicy(**link) for link in links])
        await db.flush()

    @staticmethod
    async def bulk_save_message_evidences(
        db: AsyncSession, evidences: list[dict]
    ) -> None:
        if not evidences:
            return
        db.add_all([ChatMessageEvidence(**ev) for ev in evidences])
        await db.flush()

    @staticmethod
    async def find_policies_by_message_ids(
        db: AsyncSession, message_ids: list[int]
    ) -> dict[int, list[dict]]:
        if not message_ids:
            return {}
        result = await db.execute(
            text(
                """
                SELECT
                    cmp.chat_message_id,
                    cmp.policy_id,
                    cmp.action_type,
                    p.policy_code AS slug,
                    p.policy_name
                FROM chat_message_policy cmp
                JOIN policy p ON p.policy_id = cmp.policy_id
                WHERE cmp.chat_message_id = ANY(:ids)
                ORDER BY cmp.chat_message_policy_id
                """,
            ),
            {"ids": message_ids},
        )
        by_msg: dict[int, list[dict]] = {}
        for row in result.all():
            by_msg.setdefault(row.chat_message_id, []).append(
                {
                    "policy_id": str(row.policy_id),
                    "slug": row.slug,
                    "policy_name": row.policy_name,
                    "action_type": row.action_type,
                }
            )
        return by_msg

    @staticmethod
    async def find_evidences_by_message_ids(
        db: AsyncSession, message_ids: list[int]
    ) -> dict[int, list[dict]]:
        if not message_ids:
            return {}
        result = await db.execute(
            text(
                """
                SELECT
                    cme.chat_message_id,
                    cme.chunk_id,
                    cme.snippet,
                    cme.evidence_role,
                    p.policy_name AS source_title,
                    pd.source_url
                FROM chat_message_evidence cme
                JOIN policy_document_chunk pdc ON pdc.chunk_id = cme.chunk_id
                JOIN policy_document pd ON pd.document_id = pdc.document_id
                JOIN policy p ON p.policy_id = pd.policy_id
                WHERE cme.chat_message_id = ANY(:ids)
                ORDER BY cme.chat_message_evidence_id
                """,
            ),
            {"ids": message_ids},
        )
        by_msg: dict[int, list[dict]] = {}
        for row in result.all():
            by_msg.setdefault(row.chat_message_id, []).append(
                {
                    "chunk_id": str(row.chunk_id),
                    "snippet": row.snippet,
                    "evidence_role": row.evidence_role,
                    "source_title": row.source_title,
                    "source_url": row.source_url,
                }
            )
        return by_msg

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_request import ChatRequest
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository


FINAL_CHAT_REQUEST_STATUSES = {"completed", "failed", "cancelled"}
STALE_PROCESSING_MINUTES = 5


class ChatRequestRepository:
    @staticmethod
    async def create_processing(
        db: AsyncSession,
        *,
        chat_session_id: int,
        user_message_id: int,
        idempotency_key: str | None,
    ) -> ChatRequest:
        request = ChatRequest(
            chat_session_id=chat_session_id,
            user_message_id=user_message_id,
            idempotency_key=idempotency_key,
            status="processing",
        )
        db.add(request)
        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def find_by_id(
        db: AsyncSession,
        request_id: int,
    ) -> ChatRequest | None:
        result = await db.execute(
            select(ChatRequest).where(ChatRequest.request_id == request_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_by_idempotency_key(
        db: AsyncSession,
        *,
        chat_session_id: int,
        idempotency_key: str,
    ) -> ChatRequest | None:
        result = await db.execute(
            select(ChatRequest)
            .where(ChatRequest.chat_session_id == chat_session_id)
            .where(ChatRequest.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def find_latest_incomplete(
        db: AsyncSession,
        chat_session_id: int,
    ) -> ChatRequest | None:
        result = await db.execute(
            select(ChatRequest)
            .where(ChatRequest.chat_session_id == chat_session_id)
            .where(ChatRequest.status == "processing")
            .order_by(ChatRequest.created_at.desc(), ChatRequest.request_id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def lock_session_for_slot_update(db: AsyncSession, chat_session_id: int):
        result = await db.execute(
            select(ChatSession)
            .where(ChatSession.chat_session_id == chat_session_id)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def mark_completed(
        db: AsyncSession,
        request: ChatRequest,
        *,
        intent: str | None,
        assistant_message_id: int,
        response_payload_json: dict[str, Any],
    ) -> ChatRequest:
        now = _utc_now_naive()
        request.status = "completed"
        request.intent = intent
        request.error_code = None
        request.error_message = None
        request.assistant_message_id = assistant_message_id
        request.response_payload_json = response_payload_json
        request.completed_at = now
        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        request: ChatRequest,
        *,
        error_code: str,
        error_message: str,
    ) -> ChatRequest:
        request.status = "failed"
        request.error_code = error_code
        request.error_message = error_message
        request.completed_at = _utc_now_naive()
        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def mark_cancelled(db: AsyncSession, request: ChatRequest) -> ChatRequest:
        request.status = "cancelled"
        request.completed_at = _utc_now_naive()
        await db.flush()
        await db.refresh(request)
        return request

    @staticmethod
    async def mark_stale_processing_failed(db: AsyncSession) -> int:
        cutoff = _utc_now_naive() - timedelta(minutes=STALE_PROCESSING_MINUTES)
        result = await db.execute(
            text(
                """
                UPDATE chat_request
                SET status = 'failed',
                    error_code = 'STALE_PROCESSING',
                    error_message = '서버 재시작 또는 작업 중단으로 처리 상태가 만료되었습니다.',
                    completed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'processing'
                  AND updated_at < :cutoff
                """
            ),
            {"cutoff": cutoff},
        )
        return int(result.rowcount or 0)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

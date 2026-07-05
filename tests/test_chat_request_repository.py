import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.db.models.chat_request import ChatRequest
from app.repositories.chat_request_repository import ChatRequestRepository


def _request() -> ChatRequest:
    return ChatRequest(
        request_id=1,
        chat_session_id=10,
        user_message_id=20,
        status="processing",
    )


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


def test_terminal_status_timestamps_are_utc_naive() -> None:
    async def run() -> None:
        completed = _request()
        failed = _request()
        cancelled = _request()

        await ChatRequestRepository.mark_completed(
            _db(),
            completed,
            intent="recommend",
            assistant_message_id=30,
            response_payload_json={},
        )
        await ChatRequestRepository.mark_failed(
            _db(),
            failed,
            error_code="INTERNAL_SERVER_ERROR",
            error_message="failed",
        )
        await ChatRequestRepository.mark_cancelled(_db(), cancelled)

        assert completed.completed_at is not None
        assert failed.completed_at is not None
        assert cancelled.completed_at is not None
        assert completed.completed_at.tzinfo is None
        assert failed.completed_at.tzinfo is None
        assert cancelled.completed_at.tzinfo is None

    asyncio.run(run())


def test_stale_cutoff_is_utc_naive() -> None:
    async def run() -> None:
        db = _db()
        db.execute.return_value = SimpleNamespace(rowcount=0)

        await ChatRequestRepository.mark_stale_processing_failed(db)

        _, params = db.execute.await_args.args
        assert params["cutoff"].tzinfo is None

    asyncio.run(run())


def test_create_processing_does_not_execute_runtime_ddl() -> None:
    async def run() -> None:
        db = _db()

        await ChatRequestRepository.create_processing(
            db,
            chat_session_id=10,
            user_message_id=20,
            idempotency_key="request-key",
        )

        db.execute.assert_not_awaited()

    asyncio.run(run())

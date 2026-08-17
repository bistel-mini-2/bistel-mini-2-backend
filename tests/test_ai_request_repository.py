import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.repositories.ai_request_repository import AiRequestRepository
from app.schemas.ai_contract import RequestStatus


def test_mark_stale_processing_failed_uses_utc_naive_cutoff() -> None:
    async def run() -> None:
        db = AsyncMock()
        db.execute.return_value = SimpleNamespace(rowcount=3)
        repository = AiRequestRepository()

        updated = await repository.mark_stale_processing_failed(db, "recommendation")

        assert updated == 3
        _, params = db.execute.await_args.args
        assert params["cutoff"].tzinfo is None
        assert params["failed_status"] == RequestStatus.FAILED.value
        assert params["processing_status"] == RequestStatus.PROCESSING.value

    asyncio.run(run())


def test_create_accepts_idempotency_without_runtime_ddl() -> None:
    async def run() -> None:
        db = AsyncMock()
        db.add = MagicMock()
        repository = AiRequestRepository()

        request = await repository.create(
            db,
            request_type="recommendation",
            user_id=7,
            source_type="FORM",
            idempotency_key="same-key",
        )

        assert request.idempotency_key == "same-key"
        db.add.assert_called_once_with(request)
        db.execute.assert_not_awaited()

    asyncio.run(run())

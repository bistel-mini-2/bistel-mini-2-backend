import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.schemas.ai_contract import RequestStatus
from app.services import ai_request_lifecycle_service as lifecycle_module
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def _request(**overrides):
    data = {
        "request_id": 123,
        "request_status": RequestStatus.COMPLETED.value,
        "policy_id": None,
        "source_type": "FORM",
        "source_ref_id": None,
        "idempotency_key": "same-key",
        "parsed_query_json": None,
        "merged_condition_json": None,
        "profile_conflict_json": None,
        "result_json": None,
        "error_message": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def test_create_request_reuses_existing_idempotency_key(monkeypatch) -> None:
    async def run() -> None:
        existing = _request()
        repository = SimpleNamespace(
            find_by_idempotency_key=AsyncMock(return_value=existing),
            create=AsyncMock(),
        )
        service = AiRequestLifecycleService(repository=repository)
        monkeypatch.setattr(
            lifecycle_module.UserRepository,
            "find_by_id",
            AsyncMock(return_value=SimpleNamespace(user_id=7)),
        )

        snapshot = await service.create_request(
            db=AsyncMock(),
            user_id=7,
            request_type="recommendation",
            source_type="FORM",
            idempotency_key="same-key",
        )

        assert snapshot.request_id == "123"
        assert snapshot.status == RequestStatus.COMPLETED
        assert snapshot.idempotency_key == "same-key"
        repository.find_by_idempotency_key.assert_awaited_once()
        repository.create.assert_not_awaited()

    asyncio.run(run())

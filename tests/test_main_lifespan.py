import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app import main as main_module


def test_lifespan_marks_stale_chat_requests_failed(monkeypatch) -> None:
    connection = AsyncMock()
    connection_context = AsyncMock()
    connection_context.__aenter__.return_value = connection
    connection_context.__aexit__.return_value = None

    pool = MagicMock()
    pool.open = AsyncMock()
    pool.close = AsyncMock()
    pool.connection.return_value = connection_context

    db = AsyncMock()
    session_context = AsyncMock()
    session_context.__aenter__.return_value = db
    session_context.__aexit__.return_value = None
    session_factory = MagicMock(return_value=session_context)
    cleanup = AsyncMock(return_value=2)
    ai_cleanup = AsyncMock(return_value=1)

    class FakeAiRequestRepository:
        mark_stale_processing_failed = ai_cleanup

    monkeypatch.setattr(main_module, "psycopg_pool", pool)
    monkeypatch.setattr(
        main_module.PolicyRepository,
        "ensure_search_indexes",
        AsyncMock(),
    )
    monkeypatch.setattr(main_module, "AsyncSessionLocal", session_factory, raising=False)
    monkeypatch.setattr(
        main_module.ChatRequestRepository,
        "mark_stale_processing_failed",
        cleanup,
        raising=False,
    )
    monkeypatch.setattr(
        main_module,
        "AiRequestRepository",
        FakeAiRequestRepository,
    )
    monkeypatch.setattr(
        main_module,
        "engine",
        SimpleNamespace(dispose=AsyncMock()),
    )

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run())

    pool.connection.assert_called_once_with(
        timeout=main_module.STARTUP_DB_MAINTENANCE_TIMEOUT_SECONDS
    )
    cleanup.assert_awaited_once_with(db)
    assert ai_cleanup.await_args_list[0].args == (db, "recommendation")
    assert ai_cleanup.await_args_list[1].args == (db, "eligibility")
    db.commit.assert_awaited_once()


def test_lifespan_continues_when_startup_db_maintenance_fails(monkeypatch) -> None:
    pool = MagicMock()
    pool.open = AsyncMock(side_effect=RuntimeError("db unavailable"))
    pool.close = AsyncMock()

    monkeypatch.setattr(main_module, "psycopg_pool", pool)
    monkeypatch.setattr(
        main_module,
        "engine",
        SimpleNamespace(dispose=AsyncMock()),
    )

    entered = False

    async def run() -> None:
        nonlocal entered
        async with main_module.lifespan(main_module.app):
            entered = True

    asyncio.run(run())

    assert entered is True
    pool.close.assert_awaited_once()
    main_module.engine.dispose.assert_awaited_once()

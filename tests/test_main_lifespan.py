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
        "engine",
        SimpleNamespace(dispose=AsyncMock()),
    )

    async def run() -> None:
        async with main_module.lifespan(main_module.app):
            pass

    asyncio.run(run())

    cleanup.assert_awaited_once_with(db)
    db.commit.assert_awaited_once()

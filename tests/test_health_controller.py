from collections.abc import AsyncGenerator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.health_controller import REQUIRED_SCHEMA_COLUMNS, router
from app.db.session import get_db_session


def _app_with_db(db) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    async def fake_db() -> AsyncGenerator[object, None]:
        yield db

    app.dependency_overrides[get_db_session] = fake_db
    return app


def _rows_for_required_columns() -> list[tuple[str, str]]:
    return [
        (table_name, column_name)
        for table_name, columns in REQUIRED_SCHEMA_COLUMNS.items()
        for column_name in columns
    ]


def test_liveness_does_not_touch_database() -> None:
    db = SimpleNamespace(execute=lambda *args, **kwargs: None)
    client = TestClient(_app_with_db(db))

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_ready_when_db_and_schema_are_available() -> None:
    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def all(self):
            return self._rows

    class FakeDb:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResult()
            return FakeResult(_rows_for_required_columns())

    client = TestClient(_app_with_db(FakeDb()))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_readiness_returns_503_when_schema_column_is_missing() -> None:
    class FakeResult:
        def __init__(self, rows=None):
            self._rows = rows or []

        def all(self):
            return self._rows

    class FakeDb:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return FakeResult()
            rows = [
                row
                for row in _rows_for_required_columns()
                if row != ("recommendation_request", "idempotency_key")
            ]
            return FakeResult(rows)

    client = TestClient(_app_with_db(FakeDb()))

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["schema"]["missing"] == {
        "recommendation_request": ["idempotency_key"]
    }


def test_readiness_returns_503_when_database_ping_fails() -> None:
    class FakeDb:
        async def execute(self, *args, **kwargs):
            raise RuntimeError("db unavailable")

    client = TestClient(_app_with_db(FakeDb()))

    response = client.get("/health/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["status"] == "error"
    assert body["checks"]["database"]["error"] == "RuntimeError"
    assert body["checks"]["schema"]["status"] == "not_checked"

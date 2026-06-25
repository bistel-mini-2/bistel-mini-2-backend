from collections.abc import AsyncGenerator
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.compare_controller import history_router, public_router, router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user, get_optional_current_user
from app.db.session import get_db_session
from app.schemas.compare_schema import (
    CompareHistoryItem,
    ComparePolicySummary,
    PolicyCompareResponse,
)
from app.services.compare_service import CompareService


async def fake_db() -> AsyncGenerator[object, None]:
    yield object()


async def fake_current_user():
    return SimpleNamespace(user_id=7)


async def fake_optional_current_user():
    return SimpleNamespace(user_id=7)


def create_app(*, authenticated: bool = True, optional_user: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.include_router(public_router)
    app.include_router(history_router)
    app.dependency_overrides[get_db_session] = fake_db
    if authenticated:
        app.dependency_overrides[get_current_user] = fake_current_user
    if optional_user:
        app.dependency_overrides[get_optional_current_user] = fake_optional_current_user
    register_exception_handlers(app)
    return app


def make_compare_response() -> PolicyCompareResponse:
    return PolicyCompareResponse(
        policy_a=ComparePolicySummary(
            policy_id="1",
            slug="WLF00000001",
            name="A 정책",
            summary={"benefit": "A 혜택", "condition": "A 대상"},
        ),
        policy_b=ComparePolicySummary(
            policy_id="2",
            slug="WLF00000002",
            name="B 정책",
            summary={"benefit": "B 혜택", "condition": "B 대상"},
        ),
        diff_table=[],
        selection_guide="비교 안내",
        related_policies=[],
    )


def test_compare_saves_history_for_optional_authenticated_user(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_compare(self, db, *, slug_a: str, slug_b: str, user_id: int | None):
        captured.update({"slug_a": slug_a, "slug_b": slug_b, "user_id": user_id})
        return make_compare_response()

    monkeypatch.setattr(CompareService, "compare_policies", fake_compare)

    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/compare",
            params={"a": "WLF00000001", "b": "WLF00000002"},
        )

    assert response.status_code == 200
    assert captured == {
        "slug_a": "WLF00000001",
        "slug_b": "WLF00000002",
        "user_id": 7,
    }


def test_compare_allows_anonymous_user_without_history(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_compare(self, db, *, slug_a: str, slug_b: str, user_id: int | None):
        captured.update({"slug_a": slug_a, "slug_b": slug_b, "user_id": user_id})
        return make_compare_response()

    monkeypatch.setattr(CompareService, "compare_policies", fake_compare)

    with TestClient(create_app(optional_user=False)) as client:
        response = client.get(
            "/compare",
            params={"a": "WLF00000001", "b": "WLF00000002"},
        )

    assert response.status_code == 200
    assert captured["user_id"] is None


def test_get_compare_history_returns_items_and_pagination(monkeypatch) -> None:
    async def fake_get_history(self, db, *, user_id: int, page: int, size: int):
        assert (user_id, page, size) == (7, 2, 10)
        return (
            [
                CompareHistoryItem(
                    id="3",
                    policy_a_name="A 정책",
                    policy_b_name="B 정책",
                    policy_a_slug="WLF00000001",
                    policy_b_slug="WLF00000002",
                    compared_at=datetime(2026, 6, 25, 10, 30, 0),
                )
            ],
            21,
        )

    monkeypatch.setattr(CompareService, "get_compare_history", fake_get_history)

    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/users/me/compare-history",
            params={"page": 2, "size": 10},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0] == {
        "id": "3",
        "policy_a_name": "A 정책",
        "policy_b_name": "B 정책",
        "policy_a_slug": "WLF00000001",
        "policy_b_slug": "WLF00000002",
        "compared_at": "2026-06-25T10:30:00",
    }
    assert body["meta"] == {
        "page": 2,
        "size": 10,
        "total": 21,
        "total_pages": 3,
    }


def test_get_compare_history_requires_authentication() -> None:
    with TestClient(create_app(authenticated=False)) as client:
        response = client.get("/api/v1/users/me/compare-history")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"

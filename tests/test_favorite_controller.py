from collections.abc import AsyncGenerator
from datetime import datetime
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.favorite_controller import favorites_router, user_favorites_router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.favorite_schema import (
    FavoriteCreateResponse,
    FavoriteDeleteResponse,
    FavoritePolicyResponse,
)
from app.services.favorite_service import FavoriteService


async def fake_db() -> AsyncGenerator[object, None]:
    yield object()


async def fake_current_user():
    return SimpleNamespace(user_id=7)


def create_app(*, authenticated: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(favorites_router)
    app.include_router(user_favorites_router)
    app.dependency_overrides[get_db_session] = fake_db
    if authenticated:
        app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)
    return app


def test_add_favorite_returns_created_response(monkeypatch) -> None:
    saved_at = datetime(2026, 6, 22, 12, 0, 0)

    async def fake_add(db, *, user_id: int, policy_slug: str):
        assert user_id == 7
        assert policy_slug == "WLF00000024"
        return FavoriteCreateResponse(
            policy_id="1",
            policy_slug=policy_slug,
            policy_name="테스트 정책",
            category="생활지원",
            region="national",
            saved_at=saved_at,
        )

    monkeypatch.setattr(FavoriteService, "add", fake_add)

    with TestClient(create_app()) as client:
        response = client.post("/api/v1/favorites/WLF00000024")

    assert response.status_code == 201
    assert response.json()["data"] == {
        "policy_id": "1",
        "policy_slug": "WLF00000024",
        "policy_name": "테스트 정책",
        "category": "생활지원",
        "region": "national",
        "saved_at": "2026-06-22T12:00:00",
    }


def test_remove_favorite_returns_removed_response(monkeypatch) -> None:
    async def fake_remove(db, *, user_id: int, policy_slug: str):
        return FavoriteDeleteResponse(
            policy_slug=policy_slug,
            removed=True,
        )

    monkeypatch.setattr(FavoriteService, "remove", fake_remove)

    with TestClient(create_app()) as client:
        response = client.delete("/api/v1/favorites/WLF00000024")

    assert response.status_code == 200
    assert response.json()["data"] == {
        "policy_slug": "WLF00000024",
        "removed": True,
    }


def test_get_my_favorites_returns_items_and_pagination(monkeypatch) -> None:
    async def fake_get_list(db, *, user_id: int, page: int, size: int):
        assert (user_id, page, size) == (7, 2, 10)
        return (
            [
                FavoritePolicyResponse(
                    policy_id="1",
                    policy_slug="WLF00000024",
                    policy_name="테스트 정책",
                    category="생활지원",
                    region="national",
                    saved_at=datetime(2026, 6, 22, 12, 0, 0),
                )
            ],
            21,
        )

    monkeypatch.setattr(FavoriteService, "get_list", fake_get_list)

    with TestClient(create_app()) as client:
        response = client.get(
            "/api/v1/users/me/favorites",
            params={"page": 2, "size": 10},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["items"][0] == {
        "policy_id": "1",
        "policy_slug": "WLF00000024",
        "policy_name": "테스트 정책",
        "category": "생활지원",
        "region": "national",
        "saved_at": "2026-06-22T12:00:00",
    }
    assert body["meta"] == {
        "page": 2,
        "size": 10,
        "total": 21,
        "total_pages": 3,
    }


def test_favorite_endpoints_require_authentication() -> None:
    with TestClient(create_app(authenticated=False)) as client:
        responses = [
            client.post("/api/v1/favorites/WLF00000024"),
            client.delete("/api/v1/favorites/WLF00000024"),
            client.get("/api/v1/users/me/favorites"),
        ]

    assert [response.status_code for response in responses] == [401, 401, 401]
    assert all(
        response.json()["error"]["code"] == "UNAUTHORIZED"
        for response in responses
    )

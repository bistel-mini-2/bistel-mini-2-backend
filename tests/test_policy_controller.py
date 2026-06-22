from collections.abc import AsyncGenerator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.policy_controller as policy_controller
from app.api.policy_controller import router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.policy_schema import PolicyListItemResponse, PolicySort
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService
from app.services.policy_service import PolicyService


def test_policy_list_returns_paginated_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        yield object()

    async def fake_get_policy_list(
        self,
        db,
        **kwargs,
    ) -> tuple[list[PolicyListItemResponse], int]:
        captured.update(kwargs)
        return (
            [
                PolicyListItemResponse(
                    policy_id="1",
                    slug="WLF00000024",
                    name="테스트 정책",
                    category="생활지원",
                    tags=["영유아"],
                    summary="정책 요약",
                ),
            ],
            21,
        )

    monkeypatch.setattr(PolicyService, "get_policy_list", fake_get_policy_list)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = fake_db
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/policies",
            params={
                "query": "지원",
                "category": "생활지원",
                "tags": ["영유아", "아동"],
                "region_code": "national",
                "sort": "name",
                "page": 2,
                "size": 10,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert "items" not in body
    assert body["data"][0]["slug"] == "WLF00000024"
    assert body["meta"] == {
        "page": 2,
        "size": 10,
        "total": 21,
        "total_pages": 3,
    }
    assert captured == {
        "query": "지원",
        "category": "생활지원",
        "tags": ["영유아", "아동"],
        "region_code": "national",
        "stage": None,
        "sort": PolicySort.NAME,
        "page": 2,
        "size": 10,
    }


def test_policy_list_accepts_issue_29_compatible_aliases(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        yield object()

    async def fake_get_policy_list(self, db, **kwargs):
        captured.update(kwargs)
        return ([], 0)

    monkeypatch.setattr(PolicyService, "get_policy_list", fake_get_policy_list)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = fake_db

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/policies",
            params={
                "q": "출산",
                "category": "임신·출산",
                "region": "national",
                "stage": "pregnant",
                "page": 2,
                "size": 10,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["data"] == []
    assert body["meta"] == {
        "page": 2,
        "size": 10,
        "total": 0,
        "total_pages": 0,
    }
    assert captured == {
        "query": "출산",
        "category": "임신·출산",
        "tags": None,
        "region_code": "national",
        "stage": "pregnant",
        "sort": PolicySort.UPDATED_AT,
        "page": 2,
        "size": 10,
    }


def test_policy_list_prefers_canonical_parameters_over_aliases(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        yield object()

    async def fake_get_policy_list(self, db, **kwargs):
        captured.update(kwargs)
        return ([], 0)

    monkeypatch.setattr(PolicyService, "get_policy_list", fake_get_policy_list)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = fake_db

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/policies",
            params={
                "query": "canonical-query",
                "q": "alias-query",
                "region_code": "seoul",
                "region": "busan",
            },
        )

    assert response.status_code == 200
    assert captured["query"] == "canonical-query"
    assert captured["region_code"] == "seoul"


def test_policy_list_rejects_invalid_parameters() -> None:
    app = FastAPI()
    app.include_router(router)
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/policies?page=0&size=101&sort=deadline"
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_policy_eligibility_request_returns_loading(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        db = SimpleNamespace(commit=lambda: None)
        async def commit():
            captured["committed"] = True
        db.commit = commit
        yield db

    async def fake_current_user() -> object:
        return SimpleNamespace(user_id=7)

    async def fake_create_eligibility_request(
        self,
        db,
        *,
        user_id,
        policy_identifier,
        source_type,
        selected_conditions,
        **kwargs,
    ):
        captured["create"] = {
            "user_id": user_id,
            "policy_identifier": policy_identifier,
            "source_type": source_type,
            "selected_conditions": selected_conditions,
        }
        return SimpleNamespace(request_id="123")

    async def fake_mark_processing(self, db, request_type, request_id):
        captured["mark_processing"] = {
            "request_type": request_type,
            "request_id": request_id,
        }
        return SimpleNamespace(request_id=str(request_id))

    async def fake_process_policy_eligibility_request(request_id: int) -> None:
        captured["background_request_id"] = request_id

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "create_eligibility_request",
        fake_create_eligibility_request,
    )
    monkeypatch.setattr(
        AiRequestLifecycleService,
        "mark_processing",
        fake_mark_processing,
    )
    monkeypatch.setattr(
        policy_controller,
        "process_policy_eligibility_request",
        fake_process_policy_eligibility_request,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/policies/WLF00000024/eligibility",
            json={
                "user_conditions": {
                    "stage": "newborn",
                    "child_age": "0",
                    "income": "mid1",
                    "region": "seoul",
                    "special": ["many"],
                }
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"] == {
        "request_id": "123",
        "status": "loading",
    }
    assert captured["create"] == {
        "user_id": 7,
        "policy_identifier": "WLF00000024",
        "source_type": "POLICY_DETAIL",
        "selected_conditions": {
            "stage": "newborn",
            "child_age": "0",
            "income": "mid1",
            "region": "seoul",
            "special": ["many"],
        },
    }
    assert captured["mark_processing"] == {
        "request_type": "eligibility",
        "request_id": 123,
    }
    assert captured["committed"] is True
    assert captured["background_request_id"] == 123

from collections.abc import AsyncGenerator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.policy_controller import router
from app.common.exceptions import register_exception_handlers
from app.db.session import get_db_session
from app.schemas.policy_schema import (
    PolicyDetailResponse,
    PolicyListItemResponse,
    PolicySearchScope,
    PolicySort,
)
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
        "detail_query": None,
        "category": "생활지원",
        "tags": ["영유아", "아동"],
        "region_code": "national",
        "stage": None,
        "sort": PolicySort.NAME,
        "page": 2,
        "size": 10,
        "search_scope": PolicySearchScope.NAME,
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
                "detail_q": "서류",
                "category": "임신·출산",
                "region": "national",
                "stage": "pregnant",
                "searchScope": "all",
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
        "detail_query": "서류",
        "category": "임신·출산",
        "tags": None,
        "region_code": "national",
        "stage": "pregnant",
        "sort": PolicySort.UPDATED_AT,
        "page": 2,
        "size": 10,
        "search_scope": PolicySearchScope.ALL,
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


def test_policy_detail_returns_policy(monkeypatch) -> None:
    async def fake_db() -> AsyncGenerator[object, None]:
        yield object()

    async def fake_get_policy_detail(self, db, *, policy_slug: str):
        assert policy_slug == "WLF00000024"
        return PolicyDetailResponse(
            policy_id="1",
            slug=policy_slug,
            name="테스트 정책",
            category="생활지원",
            tags=["영유아"],
            summary="쉬운 요약",
            agency="보건복지부",
            target_description="지원 대상",
            benefit_description="지원 내용",
            application_method="온라인 신청",
            application_period_text="상시 신청",
            contact="129",
        )

    monkeypatch.setattr(
        PolicyService,
        "get_policy_detail",
        fake_get_policy_detail,
    )

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = fake_db

    with TestClient(app) as client:
        response = client.get("/api/v1/policies/WLF00000024")

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["slug"] == "WLF00000024"
    assert body["data"]["target_description"] == "지원 대상"

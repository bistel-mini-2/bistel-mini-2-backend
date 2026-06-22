from collections.abc import AsyncGenerator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ai_request_controller as ai_request_controller
from app.api.ai_request_controller import eligibility_router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user
from app.db.session import get_db_session
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def test_create_eligibility_request_uses_common_lifecycle(monkeypatch) -> None:
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
        source_ref_id,
        raw_query,
        selected_conditions,
    ):
        captured["create"] = {
            "user_id": user_id,
            "policy_identifier": policy_identifier,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "raw_query": raw_query,
            "selected_conditions": selected_conditions,
        }
        return SimpleNamespace(
            request_id="123",
            status=SimpleNamespace(value="READY"),
        )

    async def fake_mark_processing(self, db, request_type, request_id):
        captured["mark_processing"] = {
            "request_type": request_type,
            "request_id": request_id,
        }
        return SimpleNamespace(
            request_id=str(request_id),
            status=SimpleNamespace(value="PROCESSING"),
        )

    async def fake_process_ai_condition_request(
        request_type: str,
        request_id: int,
    ) -> None:
        captured["background"] = {
            "request_type": request_type,
            "request_id": request_id,
        }

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
        ai_request_controller,
        "process_ai_condition_request",
        fake_process_ai_condition_request,
    )

    app = FastAPI()
    app.include_router(eligibility_router)
    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)

    user_conditions = {
        "stage": "newborn",
        "child_age": "0",
        "income": "mid1",
        "region": "seoul",
        "special": ["many"],
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/eligibility/requests",
            json={
                "policy_id": "WLF00000024",
                "user_conditions": user_conditions,
                "source_ref_id": "WLF00000024",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["request_id"] == "123"
    assert body["data"]["status"]["value"] == "PROCESSING"
    assert body["meta"] == {
        "request_id": "123",
        "follow_up_required": False,
    }
    assert captured["create"] == {
        "user_id": 7,
        "policy_identifier": "WLF00000024",
        "source_type": "POLICY_DETAIL",
        "source_ref_id": "WLF00000024",
        "raw_query": None,
        "selected_conditions": user_conditions,
    }
    assert captured["mark_processing"] == {
        "request_type": "eligibility",
        "request_id": 123,
    }
    assert captured["committed"] is True
    assert captured["background"] == {
        "request_type": "eligibility",
        "request_id": 123,
    }

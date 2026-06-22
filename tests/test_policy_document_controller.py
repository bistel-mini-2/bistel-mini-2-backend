from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.policy_document_controller import router
from app.schemas.policy_document_schema import PolicyReferenceDocumentIngestResponse
from app.services.policy_document_service import PolicyDocumentService


def test_ingest_policy_reference_documents_with_openai_vision(monkeypatch) -> None:
    captured: dict[str, int] = {}

    async def fake_ingest(self, limit: int):
        captured["limit"] = limit
        return PolicyReferenceDocumentIngestResponse(
            requested_url_count=1,
            completed_count=0,
            skipped_count=1,
            failed_count=0,
            items=[],
            skipped=[],
            failed=[],
        )

    monkeypatch.setattr(
        PolicyDocumentService,
        "ingest_policy_reference_documents_with_openai_vision",
        fake_ingest,
    )

    app = FastAPI()
    app.include_router(router)

    with TestClient(app) as client:
        response = client.post(
            "/admin/policies/documents/references/vision/ingest",
            params={"limit": 3},
        )

    assert response.status_code == 200
    assert captured["limit"] == 3
    body = response.json()
    assert body["success"] is True
    assert body["data"]["requested_url_count"] == 1
    assert body["data"]["skipped_count"] == 1

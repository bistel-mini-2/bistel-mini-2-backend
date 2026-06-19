import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.services.policy_rag_service import PolicyRagServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/policies/rag", tags=["Policy RAG"])


@router.post("/embeddings/ingest")
async def ingest_policy_rag_embeddings(
    service: PolicyRagServiceDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    source_type: str | None = None,
) -> JSONResponse:
    logger.info("Ingest policy RAG embeddings")
    result = await service.ingest_embeddings(limit=limit, source_type=source_type)
    return success_response(data=result)


@router.get("/search")
async def search_policy_rag(
    service: PolicyRagServiceDep,
    query: Annotated[str, Query(min_length=1)],
    k: Annotated[int, Query(ge=1, le=20)] = 5,
    source_type: str | None = None,
) -> JSONResponse:
    result = await service.search(query=query, k=k, source_type=source_type)
    return success_response(data=result)

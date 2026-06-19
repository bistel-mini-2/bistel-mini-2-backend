import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.services.policy_document_service import PolicyDocumentServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/policies/documents", tags=["Policy Documents"])


@router.post("/chunks/ingest")
async def ingest_policy_detail_chunks(
    service: PolicyDocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> JSONResponse:
    logger.info("Ingest policy detail chunks")
    result = await service.ingest_policy_detail_chunks(limit=limit)
    return success_response(data=result)

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
    rebuild: bool = False,
) -> JSONResponse:
    logger.info("정책 상세 chunk 생성 시작")
    result = await service.ingest_policy_detail_chunks(
        limit=limit,
        rebuild=rebuild,
    )
    return success_response(data=result)


@router.post("/references/ingest")
async def ingest_policy_reference_documents(
    service: PolicyDocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    rebuild: bool = False,
) -> JSONResponse:
    logger.info("정책 관련 문서 텍스트 추출 및 chunk 생성 시작")
    result = await service.ingest_policy_reference_documents(
        limit=limit,
        rebuild=rebuild,
    )
    return success_response(data=result)


@router.post("/references/vision/ingest")
async def ingest_policy_reference_documents_with_openai_vision(
    service: PolicyDocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> JSONResponse:
    logger.info("OpenAI Vision 기반 정책 관련 문서 텍스트 추출 시작")
    result = await service.ingest_policy_reference_documents_with_openai_vision(
        limit=limit,
    )
    return success_response(data=result)

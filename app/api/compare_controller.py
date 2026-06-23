from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.common.schemas import ApiResponse
from app.core.dependencies import DbSessionDep
from app.schemas.compare_schema import PolicyCompareResponse
from app.services.compare_service import CompareServiceDep


router = APIRouter(prefix="/api/v1/compare", tags=["Policy Compare"])
public_router = APIRouter(prefix="/compare", tags=["Policy Compare"])


async def _compare_policies(
    db: DbSessionDep,
    service: CompareServiceDep,
    a: str,
    b: str,
) -> JSONResponse:
    result = await service.compare_policies(db, slug_a=a, slug_b=b)
    return success_response(data=result)


@router.get("", response_model=ApiResponse[PolicyCompareResponse])
async def compare_policies(
    db: DbSessionDep,
    service: CompareServiceDep,
    a: Annotated[str, Query(min_length=1, max_length=100)],
    b: Annotated[str, Query(min_length=1, max_length=100)],
) -> JSONResponse:
    return await _compare_policies(db, service, a, b)


@public_router.get("", response_model=ApiResponse[PolicyCompareResponse])
async def compare_policies_alias(
    db: DbSessionDep,
    service: CompareServiceDep,
    a: Annotated[str, Query(min_length=1, max_length=100)],
    b: Annotated[str, Query(min_length=1, max_length=100)],
) -> JSONResponse:
    return await _compare_policies(db, service, a, b)

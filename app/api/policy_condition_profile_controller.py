import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.services.policy_condition_profile_service import (
    PolicyConditionProfileServiceDep,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/policies/conditions/profiles",
    tags=["Policy Condition Profiles"],
)


@router.post("/ingest")
async def ingest_policy_condition_profiles(
    service: PolicyConditionProfileServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    overwrite: bool = False,
) -> JSONResponse:
    logger.info("정책 조건 profile 생성 시작")
    result = await service.ingest_condition_profiles(
        limit=limit,
        overwrite=overwrite,
    )
    return success_response(data=result)

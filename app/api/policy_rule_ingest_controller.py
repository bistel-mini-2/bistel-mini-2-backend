import logging
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.services.policy_rule_ingest_service import PolicyRuleIngestServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/admin/policies/conditions/rules",
    tags=["Policy Condition Rules"],
)


@router.post("/ingest")
async def ingest_policy_condition_rules(
    service: PolicyRuleIngestServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
    overwrite: bool = False,
) -> JSONResponse:
    logger.info("condition_json 기반 policy_rule 파생 저장 시작")
    result = await service.ingest_rules(limit=limit, overwrite=overwrite)
    return success_response(data=result)

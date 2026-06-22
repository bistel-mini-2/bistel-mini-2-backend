import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.schemas.policy_judgement_schema import PolicyJudgementRequest
from app.services.policy_judgement_service import PolicyJudgementServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/policies/rag", tags=["Policy Judgement"])


@router.post("/judgement")
async def judge_policy(
    request: PolicyJudgementRequest,
    service: PolicyJudgementServiceDep,
) -> JSONResponse:
    logger.info("RAG 근거 기반 정책 판단 시작")
    result = await service.judge(request)
    return success_response(data=result)

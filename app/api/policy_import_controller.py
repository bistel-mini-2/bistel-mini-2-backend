import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.services.policy_import_service import PolicyImportServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/policies/raw", tags=["Policy Import"])


@router.post("/import")
async def import_raw_policies(service: PolicyImportServiceDep) -> JSONResponse:
    logger.info("Import raw policy data into policy tables")
    result = await service.import_raw_policies()
    return success_response(data=result)

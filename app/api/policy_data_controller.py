import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse, Response

from app.common.response import success_response
from app.core.dependencies import DbSessionDep
from app.schemas.policy_data_schema import (
    PolicyRawDetailSaveResponse,
    PolicyRawListSaveResponse,
    PolicyRawPendingSaveResponse,
)
from app.services.policy_data_service import PolicyDataService
from app.services.policy_import_service import PolicyImportServiceDep


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/policies/raw", tags=["Policy Raw Import"])


@router.get("/page", include_in_schema=False)
async def policy_raw_import_page() -> FileResponse:
    page_path = Path(__file__).resolve().parents[1] / "static" / "policy_raw_import.html"
    return FileResponse(page_path)


@router.get("/init-db")
async def init_db(db: DbSessionDep) -> dict[str, str]:
    await PolicyDataService.create_raw_import_table(db)
    return {"message": "policy_raw_import table is ready"}


@router.post("/import")
async def import_raw_policies(service: PolicyImportServiceDep) -> JSONResponse:
    result = await service.import_raw_policies()
    return success_response(data=result)


@router.get("/list")
async def welfare_list(
    call_tp: Annotated[str, Query(alias="callTp")] = "L",
    page_no: Annotated[int, Query(alias="pageNo", ge=1)] = 1,
    num_of_rows: Annotated[int, Query(alias="numOfRows", ge=1)] = 10,
    srch_key_code: Annotated[str, Query(alias="srchKeyCode")] = "001",
    search_wrd: Annotated[str | None, Query(alias="searchWrd")] = None,
    life_array: Annotated[str | None, Query(alias="lifeArray")] = None,
    trgter_indvdl_array: Annotated[
        str | None,
        Query(alias="trgterIndvdlArray"),
    ] = None,
    intrs_thema_array: Annotated[
        str | None,
        Query(alias="intrsThemaArray"),
    ] = None,
    age: Annotated[str | None, Query()] = None,
    onap_psblt_yn: Annotated[str | None, Query(alias="onapPsbltYn")] = None,
    order_by: Annotated[str | None, Query(alias="orderBy")] = None,
) -> Response:
    logger.info("Fetch welfare policy list XML")
    xml_text = PolicyDataService.get_welfare_list_xml(
        call_tp=call_tp,
        page_no=page_no,
        num_of_rows=num_of_rows,
        srch_key_code=srch_key_code,
        search_wrd=search_wrd,
        life_array=life_array,
        trgter_indvdl_array=trgter_indvdl_array,
        intrs_thema_array=intrs_thema_array,
        age=age,
        onap_psblt_yn=onap_psblt_yn,
        order_by=order_by,
    )
    return Response(content=xml_text, media_type="application/xml")


@router.get("/list/save", response_model=PolicyRawListSaveResponse)
async def welfare_list_save(
    db: DbSessionDep,
    call_tp: Annotated[str, Query(alias="callTp")] = "L",
    page_no: Annotated[int, Query(alias="pageNo", ge=1)] = 1,
    num_of_rows: Annotated[int, Query(alias="numOfRows", ge=1)] = 10,
    srch_key_code: Annotated[str, Query(alias="srchKeyCode")] = "001",
    search_wrd: Annotated[str | None, Query(alias="searchWrd")] = None,
    life_array: Annotated[str | None, Query(alias="lifeArray")] = None,
    trgter_indvdl_array: Annotated[
        str | None,
        Query(alias="trgterIndvdlArray"),
    ] = None,
    intrs_thema_array: Annotated[
        str | None,
        Query(alias="intrsThemaArray"),
    ] = None,
    age: Annotated[str | None, Query()] = None,
    onap_psblt_yn: Annotated[str | None, Query(alias="onapPsbltYn")] = None,
    order_by: Annotated[str | None, Query(alias="orderBy")] = None,
) -> PolicyRawListSaveResponse:
    logger.info("Fetch and save welfare policy list")
    await PolicyDataService.create_raw_import_table(db)
    xml_text = PolicyDataService.get_welfare_list_xml(
        call_tp=call_tp,
        page_no=page_no,
        num_of_rows=num_of_rows,
        srch_key_code=srch_key_code,
        search_wrd=search_wrd,
        life_array=life_array,
        trgter_indvdl_array=trgter_indvdl_array,
        intrs_thema_array=intrs_thema_array,
        age=age,
        onap_psblt_yn=onap_psblt_yn,
        order_by=order_by,
    )
    result = await PolicyDataService.save_list_xml_to_db(db, xml_text)
    return PolicyRawListSaveResponse(**result)


@router.get("/detail")
async def welfare_detail(
    call_tp: Annotated[str, Query(alias="callTp")] = "D",
    serv_id: Annotated[str, Query(alias="servId")] = "",
) -> Response:
    logger.info("Fetch welfare policy detail XML")
    xml_text = PolicyDataService.get_welfare_detail_xml(
        call_tp=call_tp,
        serv_id=serv_id,
    )
    return Response(content=xml_text, media_type="application/xml")


@router.get("/detail/save", response_model=PolicyRawDetailSaveResponse)
async def welfare_detail_save(
    db: DbSessionDep,
    call_tp: Annotated[str, Query(alias="callTp")] = "D",
    serv_id: Annotated[str, Query(alias="servId")] = "",
) -> PolicyRawDetailSaveResponse:
    logger.info("Fetch and save welfare policy detail")
    await PolicyDataService.create_raw_import_table(db)
    xml_text = PolicyDataService.get_welfare_detail_xml(
        call_tp=call_tp,
        serv_id=serv_id,
    )
    result = await PolicyDataService.save_detail_xml_to_db(db, serv_id, xml_text)
    return PolicyRawDetailSaveResponse(**result)


@router.get("/detail/save-pending", response_model=PolicyRawPendingSaveResponse)
async def welfare_detail_save_pending(
    db: DbSessionDep,
    call_tp: Annotated[str, Query(alias="callTp")] = "D",
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> PolicyRawPendingSaveResponse:
    logger.info("Fetch and save pending welfare policy details")
    await PolicyDataService.create_raw_import_table(db)
    serv_ids = await PolicyDataService.get_pending_serv_ids(db, limit)

    completed: list[str] = []
    failed: list[dict[str, str]] = []
    for serv_id in serv_ids:
        try:
            xml_text = PolicyDataService.get_welfare_detail_xml(
                call_tp=call_tp,
                serv_id=serv_id,
            )
            await PolicyDataService.save_detail_xml_to_db(db, serv_id, xml_text)
            completed.append(serv_id)
        except Exception as exc:
            await PolicyDataService.save_detail_failed(db, serv_id, str(exc))
            failed.append({"serv_id": serv_id, "error": str(exc)})

    return PolicyRawPendingSaveResponse(
        completed_count=len(completed),
        failed_count=len(failed),
        completed=completed,
        failed=failed,
    )

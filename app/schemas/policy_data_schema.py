from pydantic import BaseModel, Field


class PolicyRawListSaveResponse(BaseModel):
    saved_count: int
    skipped_count: int


class PolicyRawDetailSaveResponse(BaseModel):
    serv_id: str
    detail_status: str


class PolicyRawPendingSaveResponse(BaseModel):
    completed_count: int
    failed_count: int
    completed: list[str]
    failed: list[dict[str, str]]


class PolicyImportResponse(BaseModel):
    raw_count: int
    imported_policy_count: int
    imported_detail_count: int
    imported_required_document_count: int
    imported_policy_document_count: int
    imported_tag_count: int
    imported_checklist_template_count: int
    skipped_count: int


class PolicyRawListQuery(BaseModel):
    call_tp: str = Field(default="L", serialization_alias="callTp")
    page_no: int = Field(default=1, ge=1, serialization_alias="pageNo")
    num_of_rows: int = Field(default=10, ge=1, serialization_alias="numOfRows")
    srch_key_code: str = Field(default="001", serialization_alias="srchKeyCode")
    search_wrd: str | None = Field(default=None, serialization_alias="searchWrd")
    life_array: str | None = Field(default=None, serialization_alias="lifeArray")
    trgter_indvdl_array: str | None = Field(
        default=None,
        serialization_alias="trgterIndvdlArray",
    )
    intrs_thema_array: str | None = Field(
        default=None,
        serialization_alias="intrsThemaArray",
    )
    age: str | None = None
    onap_psblt_yn: str | None = Field(
        default=None,
        serialization_alias="onapPsbltYn",
    )
    order_by: str | None = Field(default=None, serialization_alias="orderBy")

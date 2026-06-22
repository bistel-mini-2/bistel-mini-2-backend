from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class PolicySort(StrEnum):
    UPDATED_AT = "updated_at"
    NAME = "name"
    CATEGORY = "category"
    RELEVANCE = "relevance"


class PolicyListItemResponse(BaseModel):
    policy_id: str
    slug: str
    name: str
    category: str | None = None
    sub_category: str | None = None
    tags: list[str] = Field(default_factory=list)
    target_stage: list[str] = Field(default_factory=list)
    summary: str | None = None
    benefit_summary: str | None = None
    agency: str | None = None
    benefit_type: str | None = None
    application_status: str | None = None
    application_start_date: date | None = None
    application_end_date: date | None = None
    deadline: date | None = None
    application_period_text: str | None = None
    region_scope: str | None = None
    region_code: str | None = None
    region: str | None = None
    official_url: str | None = None

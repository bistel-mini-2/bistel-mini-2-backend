from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class PolicySort(StrEnum):
    UPDATED_AT = "updated_at"
    NAME = "name"
    CATEGORY = "category"
    RELEVANCE = "relevance"


class PolicySearchScope(StrEnum):
    NAME = "name"
    POLICY_NAME = "policy_name"
    TITLE = "title"
    ALL = "all"


class PolicyApplicationGuideResponse(BaseModel):
    available_display: str | None = None
    channel: str | None = None
    route: str | None = None
    required_documents: list[str] = Field(default_factory=list)
    contact: str | None = None
    notes: list[str] = Field(default_factory=list)
    summary: str | None = None


class PolicyListItemResponse(BaseModel):
    policy_id: str
    slug: str
    name: str
    category: str | None = None
    sub_category: str | None = None
    tags: list[str] = Field(default_factory=list)
    target_stage: list[str] = Field(default_factory=list)
    target_stage_display: list[str] = Field(default_factory=list)
    life_stage_display: str | None = None
    all_age: bool = False
    display_age: str | None = None
    summary: str | None = None
    target_summary: str | None = None
    benefit_summary: str | None = None
    benefit_summary_display: str | None = None
    agency: str | None = None
    provider: str | None = None
    provider_name: str | None = None
    responsible_agency: str | None = None
    benefit_type: str | None = None
    application_status: str | None = None
    application_status_display: str | None = None
    region_scope: str | None = None
    region_code: str | None = None
    region: str | None = None
    region_display: str | None = None
    official_url: str | None = None
    related_score: float | None = None
    related_reason: str | None = None
    related_match_criteria: list[str] = Field(default_factory=list)


class PolicyConditionProfileResponse(BaseModel):
    condition_json: dict[str, Any] = Field(default_factory=dict)
    target_summary: str | None = None
    confidence: float | None = None
    review_required: bool = False
    quality_flags: list[Any] = Field(default_factory=list)
    quality_flag_displays: list[str] = Field(default_factory=list)
    source_text: str | None = None
    source_fields: list[str] = Field(default_factory=list)


class PolicyDetailResponse(PolicyListItemResponse):
    contact: str | None = None
    benefit: str | None = None
    conditions: str | None = None
    how_to_apply: str | None = None
    application_summary: str | None = None
    application_guide: PolicyApplicationGuideResponse | None = None
    easy_summary: str | None = None
    target_description: str | None = None
    benefit_description: str | None = None
    application_method: str | None = None
    caution: str | None = None
    condition_profile: PolicyConditionProfileResponse | None = None
    related_policies: list[PolicyListItemResponse] = Field(default_factory=list)

from typing import Any

from pydantic import BaseModel, Field


class PolicyConditionProfileIngestItem(BaseModel):
    policy_id: int
    policy_code: str
    policy_name: str
    condition_profile_id: int
    target_summary: str | None = None
    confidence: float | None = None
    review_required: bool
    quality_flags: list[Any] = Field(default_factory=list)


class PolicyConditionProfileSkipItem(BaseModel):
    policy_id: int
    policy_code: str
    policy_name: str
    reason: str


class PolicyConditionProfileIngestResponse(BaseModel):
    requested_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    items: list[PolicyConditionProfileIngestItem]
    skipped: list[PolicyConditionProfileSkipItem]
    failed: list[dict[str, str]]

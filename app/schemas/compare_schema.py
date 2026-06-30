from datetime import datetime

from pydantic import BaseModel, Field


class ComparePolicySummary(BaseModel):
    policy_id: str
    slug: str
    name: str
    summary: dict[str, str | None]


class CompareDiffItem(BaseModel):
    field: str
    a: str | None = None
    b: str | None = None


class CompareRelatedPolicy(BaseModel):
    policy_id: str
    slug: str
    name: str


class PolicyCompareResponse(BaseModel):
    policy_a: ComparePolicySummary
    policy_b: ComparePolicySummary
    diff_table: list[CompareDiffItem] = Field(default_factory=list)
    selection_guide: str
    related_policies: list[CompareRelatedPolicy] = Field(default_factory=list)


class CompareHistoryItem(BaseModel):
    id: str
    policy_a_name: str
    policy_b_name: str
    policy_a_slug: str
    policy_b_slug: str
    selection_guide: str | None = None
    compared_at: datetime


class CompareHistoryListResponse(BaseModel):
    items: list[CompareHistoryItem] = Field(default_factory=list)

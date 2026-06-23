from pydantic import BaseModel, Field


class LlmRecommendationEvidenceItem(BaseModel):
    source_chunk_id: str
    snippet: str
    evidence_role: str | None = None


class LlmRecommendationItem(BaseModel):
    policy_id: str
    rerank_score: float = Field(ge=0, le=1)
    priority_score: float | None = Field(default=None, ge=0, le=1)
    priority_label: str | None = None
    reason_summary: str
    recommendation_reason: str | None = None
    why_recommended: str | None = None
    check_before_apply: str | None = None
    manual_check_summary: str | None = None
    used_evidence_chunk_ids: list[str] = Field(default_factory=list)
    evidences: list[LlmRecommendationEvidenceItem] = Field(default_factory=list)


class LlmRecommendationRerankResult(BaseModel):
    recommendations: list[LlmRecommendationItem] = Field(default_factory=list)
    summary_message: str | None = None

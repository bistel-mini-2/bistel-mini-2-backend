from pydantic import BaseModel, Field


class LlmRecommendationItem(BaseModel):
    policy_id: str
    rerank_score: float = Field(ge=0, le=1)
    reason_summary: str
    recommendation_reason: str | None = None
    manual_check_summary: str | None = None
    used_evidence_chunk_ids: list[str] = Field(default_factory=list)


class LlmRecommendationRerankResult(BaseModel):
    recommendations: list[LlmRecommendationItem] = Field(default_factory=list)
    summary_message: str | None = None

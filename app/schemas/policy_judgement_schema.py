from pydantic import BaseModel, Field

from app.common.ai_status import UserStatus
from app.schemas.policy_rag_schema import PolicyRagSearchResult


class PolicyJudgementRequest(BaseModel):
    question: str = Field(min_length=1)
    user_context: str | None = None
    k: int = Field(default=5, ge=1, le=10)
    source_type: str | None = "POLICY_DETAIL"


class PolicyJudgementResponse(BaseModel):
    question: str
    user_status: UserStatus
    answer: str
    summary: str
    reasons: list[str]
    missing_information: list[str]
    evidence_chunks: list[PolicyRagSearchResult]

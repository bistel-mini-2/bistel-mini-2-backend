from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.ai_contract import RequestStatus


class RecommendationRequestCreate(BaseModel):
    source_type: str = "FORM"
    source_ref_id: str | None = None
    raw_query: str | None = None
    selected_conditions: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_condition_input(self) -> "RecommendationRequestCreate":
        if not self.raw_query and not self.selected_conditions:
            raise ValueError("raw_query or selected_conditions is required")
        return self


class EligibilityRequestCreate(BaseModel):
    policy_id: int | str
    source_type: str = "POLICY_DETAIL"
    source_ref_id: str | None = None
    raw_query: str | None = None
    selected_conditions: dict[str, Any] | None = None
    user_conditions: dict[str, Any] | None = None

    @model_validator(mode="after")
    def normalize_condition_input(self) -> "EligibilityRequestCreate":
        if self.selected_conditions is None and self.user_conditions is not None:
            self.selected_conditions = self.user_conditions
        return self


class AiRequestSnapshot(BaseModel):
    request_id: str
    request_type: Literal["recommendation", "eligibility"]
    status: RequestStatus
    policy_id: str | None = None
    source_type: str | None = None
    source_ref_id: str | None = None
    parsed_query_json: dict[str, Any] = Field(default_factory=dict)
    merged_condition_json: dict[str, Any] = Field(default_factory=dict)
    profile_conflict_json: list[dict[str, Any]] = Field(default_factory=list)
    result_json: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any]] = Field(default_factory=list)
    questions: list[dict[str, Any]] = Field(default_factory=list)
    input_issues: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None


RecommendationPollingStatus = Literal["loading", "done", "error"]


class RecommendationEvidenceItem(BaseModel):
    chunk_id: int | str
    policy_id: int | str
    snippet: str
    source_title: str
    source_url: str
    score: float | None = None
    evidence_role: str | None = None


class FollowUpQuestionItem(BaseModel):
    field_name: str
    question_text: str
    reason: str | None = None
    priority: int = 0


class RecommendationResultItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    policy_id: str
    policy_name: str
    summary: str
    match_score: float | None = None
    evidence: list[RecommendationEvidenceItem] = Field(default_factory=list)
    follow_up_questions: list[FollowUpQuestionItem] = Field(default_factory=list)


class RecommendationPollingResponse(BaseModel):
    request_id: str
    status: RecommendationPollingStatus
    results: list[RecommendationResultItem] = Field(default_factory=list)
    recommendations: list[RecommendationResultItem] = Field(default_factory=list)
    follow_up_questions: list[FollowUpQuestionItem] = Field(default_factory=list)
    error_message: str | None = None

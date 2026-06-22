from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.ai_contract import RequestStatus


class AiRequestCreate(BaseModel):
    request_type: Literal["recommendation", "eligibility"] = "recommendation"
    user_id: int
    source_type: str = "FORM"
    source_ref_id: str | None = None
    raw_query: str | None = None
    selected_conditions: dict[str, Any] | None = None
    policy_id: int | str | None = None


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

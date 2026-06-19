from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.common.ai_status import AssessmentStatus, RequestStatus, UserStatus


class ConditionInput(BaseModel):
    raw_query: str | None = None
    selected_conditions: dict[str, Any] | None = None
    profile_snapshot: dict[str, Any] | None = None


class InputIssue(BaseModel):
    field_name: str
    issue_type: Literal["missing", "ambiguous", "invalid"]
    message: str
    priority: int = 0


class FollowUpCandidate(BaseModel):
    field_name: str
    question_text: str
    reason: str | None = None
    priority: int = 0


class ProfileConflict(BaseModel):
    field_name: str
    saved_value: Any | None = None
    current_value: Any | None = None
    selected_value: Any | None = None
    rule: str = "current_input_wins"
    changes_rule_filter_result: bool | None = None


class ConditionResult(BaseModel):
    parsed_query_json: dict[str, Any]
    merged_condition_json: dict[str, Any]
    input_issues: list[InputIssue] = Field(default_factory=list)
    profile_conflicts: list[ProfileConflict] = Field(default_factory=list)
    follow_up_candidates: list[FollowUpCandidate] = Field(default_factory=list)


class EvidenceChunk(BaseModel):
    chunk_id: int | str
    policy_id: int | str
    snippet: str
    source_title: str
    source_url: str
    score: float | None = None
    evidence_role: str | None = None


class AssessmentInput(BaseModel):
    merged_condition_json: dict[str, Any]
    policy_id: int | str | None = None
    policy_ids: list[int | str] | None = None
    evidence_chunks: list[EvidenceChunk] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_policy_target(self) -> "AssessmentInput":
        if self.policy_id is None and not self.policy_ids:
            raise ValueError("policy_id or policy_ids is required")
        return self


class AssessmentResult(BaseModel):
    policy_id: int | str
    assessment_status: AssessmentStatus
    user_status: UserStatus
    reason_summary: str | None = None
    matched_conditions: list[str] = Field(default_factory=list)
    missing_conditions: list[str] = Field(default_factory=list)
    conflicting_conditions: list[str] = Field(default_factory=list)
    manual_check_points: list[str] = Field(default_factory=list)
    evidences: list[EvidenceChunk] = Field(default_factory=list)


__all__ = [
    "AssessmentInput",
    "AssessmentResult",
    "AssessmentStatus",
    "ConditionInput",
    "ConditionResult",
    "EvidenceChunk",
    "FollowUpCandidate",
    "InputIssue",
    "ProfileConflict",
    "RequestStatus",
    "UserStatus",
]

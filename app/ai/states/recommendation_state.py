from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


if TYPE_CHECKING:
    from app.services.recommendation_assessment_service import (
        RecommendationPolicyAssessment,
    )
    from app.services.recommendation_candidate_service import PolicyCandidate


class RecommendationGraphState(TypedDict):
    db: AsyncSession
    request_id: int
    merged_condition_json: dict[str, Any]
    input_issues: NotRequired[list[dict[str, Any]]]
    profile_conflict_json: NotRequired[list[dict[str, Any]]]
    candidate_rows: NotRequired[list[dict[str, Any]]]
    query_terms: NotRequired[list[str]]
    candidates: NotRequired[list["PolicyCandidate"]]
    assessments: NotRequired[list["RecommendationPolicyAssessment"]]
    result_json: NotRequired[dict[str, Any]]

from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


if TYPE_CHECKING:
    from app.services.recommendation_candidate_service import PolicyCandidate


class RecommendationGraphState(TypedDict):
    db: AsyncSession
    request_id: int
    merged_condition_json: dict[str, Any]
    candidate_rows: NotRequired[list[dict[str, Any]]]
    query_terms: NotRequired[list[str]]
    candidates: NotRequired[list["PolicyCandidate"]]
    result_json: NotRequired[dict[str, Any]]

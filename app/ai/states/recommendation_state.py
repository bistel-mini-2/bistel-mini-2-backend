from typing import Any, NotRequired, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


class RecommendationGraphState(TypedDict):
    db: AsyncSession
    request_id: int
    merged_condition_json: dict[str, Any]
    input_issues: NotRequired[list[dict[str, Any]]]
    profile_conflict_json: NotRequired[list[dict[str, Any]]]
    candidate_rows: NotRequired[list[dict[str, Any]]]
    query_terms: NotRequired[list[str]]
    candidates: NotRequired[list[Any]]
    assessments: NotRequired[list[Any]]
    result_json: NotRequired[dict[str, Any]]

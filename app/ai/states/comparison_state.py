from typing import Any, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


class ComparisonGraphState(TypedDict, total=False):
    db: AsyncSession
    user_id: int | None
    slug_a: str
    slug_b: str
    raw_query: str | None
    compare_result: Any
    result_json: dict[str, Any]

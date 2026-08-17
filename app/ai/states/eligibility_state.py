from typing import Any, NotRequired, TypedDict

from sqlalchemy.ext.asyncio import AsyncSession


class EligibilityGraphState(TypedDict):
    db: AsyncSession
    user_id: int
    policy_identifier: int | str
    raw_query: str | None
    selected_conditions: NotRequired[dict[str, Any] | None]
    source_type: str
    source_ref_id: NotRequired[str | None]
    follow_up_resolved: NotRequired[bool]
    idempotency_key: NotRequired[str | None]
    request_id: NotRequired[int]
    request_status: NotRequired[str]
    result_json: NotRequired[dict[str, Any]]

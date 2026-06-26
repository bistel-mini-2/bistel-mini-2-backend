import asyncio
from typing import Any

from app.repositories.recommendation_candidate_repository import (
    RecommendationCandidateRepository,
)


class _FakeResult:
    class _Mappings:
        @staticmethod
        def all() -> list[dict[str, Any]]:
            return []

    @staticmethod
    def mappings() -> "_FakeResult._Mappings":
        return _FakeResult._Mappings()


class _FakeDb:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None):
        self.statements.append(str(statement))
        self.params.append(params or {})
        return _FakeResult()


def test_find_policy_rows_searches_condition_profile_text() -> None:
    db = _FakeDb()

    asyncio.run(
        RecommendationCandidateRepository().find_policy_rows(
            db,  # type: ignore[arg-type]
            query_terms=["birth"],
            limit=10,
        )
    )

    sql = " ".join(db.statements[0].split())
    assert "LEFT JOIN policy_condition_profile cp" in sql
    assert "cp.target_summary ILIKE :term_0" in sql
    assert "cp.source_text ILIKE :term_0" in sql
    assert "cp.condition_json AS condition_profile_json" in sql
    assert db.params[0]["term_0"] == "%birth%"


def test_find_policy_rows_by_ids_returns_condition_profile_columns() -> None:
    db = _FakeDb()

    asyncio.run(
        RecommendationCandidateRepository().find_policy_rows_by_ids(
            db,  # type: ignore[arg-type]
            policy_ids=[1, 2],
        )
    )

    sql = " ".join(db.statements[0].split())
    assert "LEFT JOIN policy_condition_profile cp" in sql
    assert "cp.target_summary AS condition_profile_target_summary" in sql
    assert "cp.source_text AS condition_profile_source_text" in sql

import asyncio

from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import PolicySort


def test_build_filters_matches_documented_contract() -> None:
    where_sql, params = PolicyRepository._build_filters(
        query_pattern="%출산%",
        category="임신·출산",
        tags=["영유아", "아동"],
        region_code="national",
        stage_tags=[],
    )

    assert "p.policy_name ILIKE" in where_sql
    assert "p.main_category ILIKE" in where_sql
    assert "pd.application_method ILIKE" in where_sql
    assert "LOWER(p.main_category) = LOWER(:category)" in where_sql
    assert where_sql.count("EXISTS") == 3
    assert "p.region_scope = 'NATIONAL'" in where_sql
    assert params == {
        "query_pattern": "%출산%",
        "category": "임신·출산",
        "tag_0": "영유아",
        "tag_1": "아동",
    }


def test_build_filters_supports_stage_tag_variants() -> None:
    where_sql, params = PolicyRepository._build_filters(
        query_pattern=None,
        category=None,
        tags=[],
        region_code=None,
        stage_tags=["임신 · 출산", "임신·출산"],
    )

    assert "FROM policy_tag stage_tag" in where_sql
    assert params == {
        "stage_tag_0": "임신 · 출산",
        "stage_tag_1": "임신·출산",
    }


def test_relevance_weights_title_above_category_and_detail() -> None:
    sql = PolicyRepository._build_relevance_sql(query="출산")

    assert "THEN 400" in sql
    assert "THEN 200" in sql
    assert "THEN 100" in sql
    assert "THEN 20" in sql


def test_sort_sql_uses_allowlisted_columns() -> None:
    assert "policy_name ASC" in PolicyRepository._build_sort_sql(
        sort=PolicySort.NAME,
        has_query=False,
    )
    assert "main_category ASC" in PolicyRepository._build_sort_sql(
        sort=PolicySort.CATEGORY,
        has_query=False,
    )
    assert "relevance_score DESC" in PolicyRepository._build_sort_sql(
        sort=PolicySort.RELEVANCE,
        has_query=True,
    )


def test_ensure_search_indexes_creates_issue_29_indexes() -> None:
    executed_sql: list[str] = []

    class FakeCursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def execute(self, sql: str) -> None:
            executed_sql.append(" ".join(sql.split()))

    class FakeConnection:
        def cursor(self) -> FakeCursor:
            return FakeCursor()

    asyncio.run(PolicyRepository.ensure_search_indexes(FakeConnection()))

    combined_sql = "\n".join(executed_sql)
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in combined_sql
    assert "policy_name gin_trgm_ops" in combined_sql
    assert "main_category gin_trgm_ops" in combined_sql
    assert "ON policy (region_code)" in combined_sql
    assert "ON policy_tag (LOWER(tag_name))" in combined_sql

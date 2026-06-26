import asyncio

from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import PolicySort


def test_build_filters_matches_documented_contract() -> None:
    where_sql, params = PolicyRepository._build_filters(
        query_pattern="%birth%",
        category="pregnancy",
        tags=["infant", "child"],
        region_code="national",
        stage_tags=[],
        stage=None,
    )

    assert "p.policy_name ILIKE" in where_sql
    assert "p.main_category ILIKE" in where_sql
    assert "pd.application_method ILIKE" in where_sql
    assert "cp.target_summary ILIKE" in where_sql
    assert "cp.source_text ILIKE" in where_sql
    assert "LOWER(p.main_category) = LOWER(:category)" in where_sql
    assert where_sql.count("EXISTS") == 3
    assert "p.region_scope = 'NATIONAL'" in where_sql
    assert params == {
        "query_pattern": "%birth%",
        "category": "pregnancy",
        "tag_0": "infant",
        "tag_1": "child",
    }


def test_build_filters_supports_condition_profile_stage_filter() -> None:
    where_sql, params = PolicyRepository._build_filters(
        query_pattern=None,
        category=None,
        tags=[],
        region_code=None,
        stage_tags=["pregnancy"],
        stage="pregnant",
    )

    assert "FROM policy_rule stage_rule" in where_sql
    assert "stage_rule.origin = 'condition_profile'" in where_sql
    assert "cp.condition_json::text" in where_sql
    assert "FROM policy_tag stage_tag" in where_sql
    assert params == {
        "stage_tag_0": "pregnancy",
        "stage_value": "pregnant",
        "stage_json_pattern": '%"pregnant"%',
        "stage_json_alias_pattern": "__no_stage_filter__",
    }


def test_build_filters_searches_youth_profile_alias_for_teen_stage() -> None:
    where_sql, params = PolicyRepository._build_filters(
        query_pattern=None,
        category=None,
        tags=[],
        region_code=None,
        stage_tags=[],
        stage="teen",
    )

    assert "stage_json_alias_pattern" in where_sql
    assert params["stage_value"] == "teen"
    assert params["stage_json_pattern"] == '%"teen"%'
    assert params["stage_json_alias_pattern"] == '%"youth"%'


def test_build_filters_searches_youth_profile_alias_for_young_adult_stage() -> None:
    _, params = PolicyRepository._build_filters(
        query_pattern=None,
        category=None,
        tags=[],
        region_code=None,
        stage_tags=[],
        stage="young_adult",
    )

    assert params["stage_value"] == "young_adult"
    assert params["stage_json_pattern"] == '%"young\\_adult"%'
    assert params["stage_json_alias_pattern"] == '%"youth"%'


def test_relevance_weights_title_above_category_and_detail() -> None:
    sql = PolicyRepository._build_relevance_sql(query="birth")

    assert "THEN 400" in sql
    assert "THEN 200" in sql
    assert "THEN 100" in sql
    assert "THEN 20" in sql
    assert "cp.target_summary ILIKE" in sql
    assert "cp.source_text ILIKE" in sql


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
    assert "policy_condition_profile_target_summary_trgm" in combined_sql
    assert "policy_condition_profile_source_text_trgm" in combined_sql


def test_related_policy_conditions_use_category_stage_and_tags() -> None:
    params: dict[str, object] = {}
    stage_sql = PolicyRepository._related_stage_condition(
        ["pregnant", "pregnant"],
        params,
    )
    tag_sql = PolicyRepository._related_tag_condition(
        ["pregnancy", "Pregnancy", "child"],
        params,
    )

    assert "FROM policy_rule related_stage_rule_0" in stage_sql
    assert "cp.condition_json::text" in stage_sql
    assert params["related_stage_0"] == "pregnant"
    assert params["related_stage_json_0"] == '%"pregnant"%'
    assert "related_stage_1" not in params

    assert "FROM policy_tag related_tag" in tag_sql
    assert "related_tag_0" in params
    assert "related_tag_2" in params
    assert "related_tag_1" not in params


def test_related_policy_region_condition_prefers_scope_then_code() -> None:
    params: dict[str, object] = {}

    assert (
        PolicyRepository._related_region_condition("NATIONAL", "seoul", params)
        == "p.region_scope = 'NATIONAL'"
    )

    local_sql = PolicyRepository._related_region_condition(
        "LOCAL",
        "seoul",
        params,
    )

    assert local_sql == "p.region_code = :related_region_code"
    assert params["related_region_code"] == "seoul"

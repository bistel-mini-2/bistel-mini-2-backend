from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.policy_schema import PolicySort


class PolicyRepository:
    @classmethod
    async def find_policy_list(
        cls,
        db: AsyncSession,
        *,
        query: str | None,
        query_pattern: str | None,
        category: str | None,
        tags: list[str],
        region_code: str | None,
        stage_tags: list[str],
        sort: PolicySort,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        where_sql, params = cls._build_filters(
            query_pattern=query_pattern,
            category=category,
            tags=tags,
            region_code=region_code,
            stage_tags=stage_tags,
        )

        count_result = await db.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                WHERE {where_sql}
                """,
            ),
            params,
        )
        total = int(count_result.scalar_one())

        params.update({"limit": size, "offset": (page - 1) * size})
        if query:
            params["query_exact"] = query

        result = await db.execute(
            text(
                f"""
                SELECT
                    p.policy_id,
                    p.policy_code AS slug,
                    p.policy_name AS name,
                    p.main_category AS category,
                    p.sub_category,
                    COALESCE(
                        (
                            SELECT jsonb_agg(pt.tag_name ORDER BY pt.tag_name)
                            FROM policy_tag pt
                            WHERE pt.policy_id = p.policy_id
                        ),
                        '[]'::jsonb
                    ) AS tags,
                    pd.easy_summary AS summary,
                    pd.benefit_description AS benefit_summary,
                    p.provider_name AS agency,
                    p.benefit_type,
                    p.application_status,
                    p.application_start_date,
                    p.application_end_date,
                    pd.application_period_text,
                    p.region_scope,
                    p.region_code,
                    p.official_url,
                    {cls._build_relevance_sql(query=query)} AS relevance_score
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                WHERE {where_sql}
                ORDER BY {cls._build_sort_sql(sort=sort, has_query=query is not None)}
                LIMIT :limit
                OFFSET :offset
                """,
            ),
            params,
        )
        return [dict(row) for row in result.mappings().all()], total

    @staticmethod
    async def ensure_search_indexes(conn) -> None:
        async with conn.cursor() as cur:
            await cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_policy_policy_name_trgm
                ON policy USING gin (policy_name gin_trgm_ops)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_policy_main_category_trgm
                ON policy USING gin (main_category gin_trgm_ops)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_policy_region_code
                ON policy (region_code)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_policy_tag_name_lower
                ON policy_tag (LOWER(tag_name))
                """
            )

    @classmethod
    def _build_filters(
        cls,
        *,
        query_pattern: str | None,
        category: str | None,
        tags: list[str],
        region_code: str | None,
        stage_tags: list[str],
    ) -> tuple[str, dict[str, Any]]:
        conditions = ["p.is_active = TRUE"]
        params: dict[str, Any] = {}

        if query_pattern:
            conditions.append(cls._search_condition())
            params["query_pattern"] = query_pattern

        if category:
            conditions.append("LOWER(p.main_category) = LOWER(:category)")
            params["category"] = category

        for index, tag in enumerate(tags):
            param_name = f"tag_{index}"
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM policy_tag filter_tag_{index}
                    WHERE filter_tag_{index}.policy_id = p.policy_id
                      AND LOWER(filter_tag_{index}.tag_name)
                          = LOWER(:{param_name})
                )
                """,
            )
            params[param_name] = tag

        if stage_tags:
            stage_conditions: list[str] = []
            for index, stage_tag in enumerate(stage_tags):
                param_name = f"stage_tag_{index}"
                stage_conditions.append(
                    f"LOWER(stage_tag.tag_name) = LOWER(:{param_name})"
                )
                params[param_name] = stage_tag
            conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM policy_tag stage_tag
                    WHERE stage_tag.policy_id = p.policy_id
                      AND ({" OR ".join(stage_conditions)})
                )
                """
            )

        if region_code == "national":
            conditions.append("p.region_scope = 'NATIONAL'")
        elif region_code:
            conditions.append("p.region_code = :region_code")
            params["region_code"] = region_code

        return " AND ".join(conditions), params

    @staticmethod
    def _search_condition() -> str:
        return """
            (
                p.policy_name ILIKE :query_pattern ESCAPE '\\'
                OR p.main_category ILIKE :query_pattern ESCAPE '\\'
                OR p.sub_category ILIKE :query_pattern ESCAPE '\\'
                OR pd.easy_summary ILIKE :query_pattern ESCAPE '\\'
                OR pd.target_description ILIKE :query_pattern ESCAPE '\\'
                OR pd.benefit_description ILIKE :query_pattern ESCAPE '\\'
                OR pd.application_method ILIKE :query_pattern ESCAPE '\\'
                OR EXISTS (
                    SELECT 1
                    FROM policy_tag search_tag
                    WHERE search_tag.policy_id = p.policy_id
                      AND search_tag.tag_name
                          ILIKE :query_pattern ESCAPE '\\'
                )
            )
        """

    @staticmethod
    def _build_relevance_sql(*, query: str | None) -> str:
        if not query:
            return "0"
        return (
            "CASE WHEN LOWER(p.policy_name) = LOWER(:query_exact) THEN 400 ELSE 0 END + "
            "CASE WHEN p.policy_name ILIKE :query_pattern ESCAPE '\\' THEN 200 ELSE 0 END + "
            "CASE WHEN p.main_category ILIKE :query_pattern ESCAPE '\\' "
            "OR p.sub_category ILIKE :query_pattern ESCAPE '\\' THEN 100 ELSE 0 END + "
            "CASE WHEN pd.easy_summary ILIKE :query_pattern ESCAPE '\\' "
            "OR pd.target_description ILIKE :query_pattern ESCAPE '\\' "
            "OR pd.benefit_description ILIKE :query_pattern ESCAPE '\\' "
            "OR pd.application_method ILIKE :query_pattern ESCAPE '\\' "
            "THEN 20 ELSE 0 END"
        )

    @staticmethod
    def _build_sort_sql(*, sort: PolicySort, has_query: bool) -> str:
        if sort == PolicySort.NAME:
            return "p.policy_name ASC, p.policy_id DESC"
        if sort == PolicySort.CATEGORY:
            return (
                "p.main_category ASC NULLS LAST, "
                "p.policy_name ASC, p.policy_id DESC"
            )
        if sort == PolicySort.RELEVANCE and has_query:
            return "relevance_score DESC, p.updated_at DESC, p.policy_id DESC"
        return "p.updated_at DESC, p.policy_id DESC"

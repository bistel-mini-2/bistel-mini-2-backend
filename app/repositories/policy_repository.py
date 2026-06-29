from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.policy_schema import PolicySort


class PolicyRepository:
    @classmethod
    async def find_ids_by_codes(
        cls,
        db: AsyncSession,
        codes: list[str],
    ) -> dict[str, int]:
        if not codes:
            return {}
        result = await db.execute(
            text(
                """
                SELECT policy_code, policy_id
                FROM policy
                WHERE policy_code = ANY(:codes)
                """,
            ),
            {"codes": codes},
        )
        return {row.policy_code: row.policy_id for row in result.all()}

    @classmethod
    async def find_policy_detail(
        cls,
        db: AsyncSession,
        *,
        policy_slug: str,
    ) -> dict[str, Any] | None:
        result = await db.execute(
            text(
                """
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
                    p.contact,
                    pd.easy_summary,
                    pd.target_description,
                    pd.benefit_description,
                    pd.application_method,
                    pd.caution,
                    cp.condition_json AS condition_profile_json,
                    cp.target_summary AS condition_profile_target_summary,
                    cp.confidence AS condition_profile_confidence,
                    cp.review_required AS condition_profile_review_required,
                    cp.quality_flags AS condition_profile_quality_flags,
                    cp.source_text AS condition_profile_source_text,
                    cp.source_fields AS condition_profile_source_fields
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
                WHERE p.policy_code = :policy_slug
                  AND p.is_active = TRUE
                """,
            ),
            {"policy_slug": policy_slug},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

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
        stage: str | None,
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
            stage=stage,
        )

        count_result = await db.execute(
            text(
                f"""
                SELECT COUNT(*)
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
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
                    cp.condition_json AS condition_profile_json,
                    {cls._build_relevance_sql(query=query)} AS relevance_score
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
                WHERE {where_sql}
                ORDER BY {cls._build_sort_sql(sort=sort, has_query=query is not None)}
                LIMIT :limit
                OFFSET :offset
                """,
            ),
            params,
        )
        return [dict(row) for row in result.mappings().all()], total

    @classmethod
    async def find_related_policies(
        cls,
        db: AsyncSession,
        *,
        excluded_policy_ids: list[int],
        category: str | None,
        region_scope: str | None,
        region_code: str | None,
        target_stages: list[str],
        tags: list[str],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "excluded_policy_ids": excluded_policy_ids or [-1],
            "limit": limit,
        }
        match_conditions: list[str] = []
        score_sql = "0"

        if category:
            params["category"] = category
            category_condition = "LOWER(p.main_category) = LOWER(:category)"
            match_conditions.append(category_condition)
            score_sql += f" + CASE WHEN {category_condition} THEN 5 ELSE 0 END"

        region_condition = cls._related_region_condition(
            region_scope,
            region_code,
            params,
        )
        if region_condition:
            match_conditions.append(region_condition)
            score_sql += f" + CASE WHEN ({region_condition}) THEN 2 ELSE 0 END"

        stage_condition = cls._related_stage_condition(target_stages, params)
        if stage_condition:
            match_conditions.append(stage_condition)
            score_sql += f" + CASE WHEN ({stage_condition}) THEN 3 ELSE 0 END"

        tag_condition = cls._related_tag_condition(tags, params)
        if tag_condition:
            match_conditions.append(tag_condition)
            score_sql += f" + CASE WHEN ({tag_condition}) THEN 1 ELSE 0 END"

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
                    cp.condition_json AS condition_profile_json,
                    ({score_sql}) AS related_score
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
                WHERE p.is_active = TRUE
                  AND p.policy_id NOT IN :excluded_policy_ids
                  AND ({' OR '.join(match_conditions) if match_conditions else 'TRUE'})
                ORDER BY related_score DESC, p.updated_at DESC, p.policy_id DESC
                LIMIT :limit
                """,
            ).bindparams(bindparam("excluded_policy_ids", expanding=True)),
            params,
        )
        return [dict(row) for row in result.mappings().all()]

    @classmethod
    async def find_policies_by_ids(
        cls,
        db: AsyncSession,
        policy_ids: list[int],
    ) -> list[dict[str, Any]]:
        """주어진 policy_id들의 목록용 표시 행을 로드한다(유사 정책 후보 로딩).

        반환 행 모양은 find_related_policies와 동일해 PolicyService._to_response가
        그대로 처리할 수 있다. 입력 순서는 보장하지 않으므로 호출부에서 재정렬한다.
        """
        if not policy_ids:
            return []
        result = await db.execute(
            text(
                """
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
                    cp.condition_json AS condition_profile_json
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
                WHERE p.is_active = TRUE
                  AND p.policy_id IN :policy_ids
                """,
            ).bindparams(bindparam("policy_ids", expanding=True)),
            {"policy_ids": policy_ids},
        )
        return [dict(row) for row in result.mappings().all()]

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
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    ix_policy_condition_profile_target_summary_trgm
                ON policy_condition_profile
                    USING gin (target_summary gin_trgm_ops)
                """
            )
            await cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    ix_policy_condition_profile_source_text_trgm
                ON policy_condition_profile
                    USING gin (source_text gin_trgm_ops)
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
        stage: str | None,
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

        if stage or stage_tags:
            stage_conditions: list[str] = []
            for index, stage_tag in enumerate(stage_tags):
                param_name = f"stage_tag_{index}"
                stage_conditions.append(
                    f"LOWER(stage_tag.tag_name) = LOWER(:{param_name})"
                )
                params[param_name] = stage_tag
            params["stage_value"] = stage or ""
            params["stage_json_pattern"] = cls._json_text_like_pattern(stage)
            params["stage_json_alias_pattern"] = cls._stage_json_alias_pattern(
                stage
            )
            conditions.append(
                f"""
                (
                    EXISTS (
                        SELECT 1
                        FROM policy_rule stage_rule
                        WHERE stage_rule.policy_id = p.policy_id
                          AND stage_rule.origin = 'condition_profile'
                          AND stage_rule.field_name = 'stage'
                          AND stage_rule.manual_check_required = FALSE
                          AND stage_rule.is_exclusion = FALSE
                          AND stage_rule.rule_type != 'UNSUPPORTED'
                          AND (
                              stage_rule.value_json =
                                  to_jsonb(CAST(:stage_value AS text))
                              OR (
                                  jsonb_typeof(stage_rule.value_json) = 'array'
                                  AND stage_rule.value_json ? :stage_value
                              )
                          )
                    )
                    OR (
                        cp.condition_json IS NOT NULL
                        AND (
                            cp.condition_json::text
                                ILIKE :stage_json_pattern ESCAPE '\\'
                            OR cp.condition_json::text
                                ILIKE :stage_json_alias_pattern ESCAPE '\\'
                        )
                    )
                    {cls._stage_tag_fallback_sql(stage_conditions)}
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
                OR cp.target_summary ILIKE :query_pattern ESCAPE '\\'
                OR cp.source_text ILIKE :query_pattern ESCAPE '\\'
                OR EXISTS (
                    SELECT 1
                    FROM policy_tag search_tag
                    WHERE search_tag.policy_id = p.policy_id
                      AND search_tag.tag_name
                          ILIKE :query_pattern ESCAPE '\\'
                )
            )
        """

    @classmethod
    def _related_stage_condition(
        cls,
        target_stages: list[str],
        params: dict[str, Any],
    ) -> str:
        stage_conditions: list[str] = []
        seen: set[str] = set()

        for index, raw_stage in enumerate(target_stages[:5]):
            stage = str(raw_stage).strip()
            if not stage or stage in seen:
                continue
            seen.add(stage)
            stage_param = f"related_stage_{index}"
            json_param = f"related_stage_json_{index}"
            alias_param = f"related_stage_alias_json_{index}"
            params[stage_param] = stage
            params[json_param] = cls._json_text_like_pattern(stage)
            params[alias_param] = cls._stage_json_alias_pattern(stage)
            stage_conditions.append(
                f"""
                EXISTS (
                    SELECT 1
                    FROM policy_rule related_stage_rule_{index}
                    WHERE related_stage_rule_{index}.policy_id = p.policy_id
                      AND related_stage_rule_{index}.origin = 'condition_profile'
                      AND related_stage_rule_{index}.field_name = 'stage'
                      AND related_stage_rule_{index}.manual_check_required = FALSE
                      AND related_stage_rule_{index}.is_exclusion = FALSE
                      AND related_stage_rule_{index}.rule_type != 'UNSUPPORTED'
                      AND (
                          related_stage_rule_{index}.value_json =
                              to_jsonb(CAST(:{stage_param} AS text))
                          OR (
                              jsonb_typeof(
                                  related_stage_rule_{index}.value_json
                              ) = 'array'
                              AND related_stage_rule_{index}.value_json
                                  ? :{stage_param}
                          )
                      )
                )
                OR (
                    cp.condition_json IS NOT NULL
                    AND (
                        cp.condition_json::text
                            ILIKE :{json_param} ESCAPE '\\'
                        OR cp.condition_json::text
                            ILIKE :{alias_param} ESCAPE '\\'
                    )
                )
                """,
            )

        if not stage_conditions:
            return ""
        return "(" + ") OR (".join(stage_conditions) + ")"

    @staticmethod
    def _related_region_condition(
        region_scope: str | None,
        region_code: str | None,
        params: dict[str, Any],
    ) -> str:
        if region_scope == "NATIONAL":
            return "p.region_scope = 'NATIONAL'"
        if region_code:
            params["related_region_code"] = region_code
            return "p.region_code = :related_region_code"
        return ""

    @staticmethod
    def _related_tag_condition(
        tags: list[str],
        params: dict[str, Any],
    ) -> str:
        tag_conditions: list[str] = []
        seen: set[str] = set()

        for index, raw_tag in enumerate(tags[:5]):
            tag = str(raw_tag).strip()
            key = tag.casefold()
            if not tag or key in seen:
                continue
            seen.add(key)
            param_name = f"related_tag_{index}"
            params[param_name] = tag
            tag_conditions.append(f"LOWER(related_tag.tag_name) = LOWER(:{param_name})")

        if not tag_conditions:
            return ""
        return f"""
            EXISTS (
                SELECT 1
                FROM policy_tag related_tag
                WHERE related_tag.policy_id = p.policy_id
                  AND ({" OR ".join(tag_conditions)})
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
            "OR cp.target_summary ILIKE :query_pattern ESCAPE '\\' "
            "OR cp.source_text ILIKE :query_pattern ESCAPE '\\' "
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

    @staticmethod
    def _json_text_like_pattern(value: str | None) -> str:
        if not value:
            return "__no_stage_filter__"
        escaped = (
            value.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f'%"{escaped}"%'

    @classmethod
    def _stage_json_alias_pattern(cls, value: str | None) -> str:
        aliases = {
            "teen": "youth",
            "young_adult": "youth",
        }
        return cls._json_text_like_pattern(aliases.get(value or ""))

    @staticmethod
    def _stage_tag_fallback_sql(stage_conditions: list[str]) -> str:
        if not stage_conditions:
            return ""
        return f"""
                    OR EXISTS (
                        SELECT 1
                        FROM policy_tag stage_tag
                        WHERE stage_tag.policy_id = p.policy_id
                          AND ({" OR ".join(stage_conditions)})
                    )
        """

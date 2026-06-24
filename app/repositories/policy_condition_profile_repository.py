import json
from typing import Any


class PolicyConditionProfileRepository:
    @staticmethod
    async def ensure_schema(conn) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS policy_condition_profile (
                        condition_profile_id bigserial PRIMARY KEY,
                        policy_id bigint NOT NULL
                            REFERENCES policy(policy_id) ON DELETE CASCADE,
                        condition_json jsonb NOT NULL DEFAULT '{}'::jsonb,
                        target_summary text,
                        confidence numeric(5, 4),
                        review_required boolean NOT NULL DEFAULT false,
                        quality_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
                        source_text text,
                        source_fields jsonb NOT NULL DEFAULT '[]'::jsonb,
                        extracted_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """
            )
            await cur.execute(
                """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                        policy_condition_profile_policy_uidx
                    ON policy_condition_profile(policy_id)
                """
            )
            await cur.execute(
                """
                    CREATE INDEX IF NOT EXISTS policy_condition_profile_review_idx
                    ON policy_condition_profile(review_required)
                """
            )
            await cur.execute(
                """
                    CREATE INDEX IF NOT EXISTS
                        policy_condition_profile_condition_gin_idx
                    ON policy_condition_profile USING GIN(condition_json)
                """
            )

    @staticmethod
    async def find_profile_targets(
        conn,
        limit: int,
        overwrite: bool = False,
    ) -> list[dict[str, Any]]:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    SELECT
                        p.policy_id,
                        p.policy_code,
                        p.policy_name,
                        p.main_category,
                        p.sub_category,
                        p.provider_name,
                        p.benefit_type,
                        p.application_status,
                        p.official_url,
                        d.easy_summary,
                        d.target_description,
                        d.benefit_description,
                        d.application_method,
                        d.application_period_text,
                        d.caution,
                        NULLIF(r.detail_json->>'lifeArray', '') AS life_array,
                        NULLIF(r.detail_json->>'trgterIndvdlArray', '')
                            AS target_individual_array,
                        NULLIF(r.detail_json->>'intrsThemaArray', '')
                            AS interest_theme_array,
                        NULLIF(r.detail_json->>'tgtrDtlCn', '')
                            AS raw_target_detail,
                        NULLIF(r.detail_json->>'slctCritCn', '')
                            AS raw_selection_criteria,
                        NULLIF(r.detail_json->>'wlfareInfoOutlCn', '')
                            AS raw_outline,
                        NULLIF(r.detail_json->>'alwServCn', '')
                            AS raw_benefit_content
                    FROM policy p
                    JOIN policy_raw_import r ON r.serv_id = p.policy_code
                    LEFT JOIN policy_detail d ON d.policy_id = p.policy_id
                    LEFT JOIN policy_condition_profile cp
                        ON cp.policy_id = p.policy_id
                    WHERE p.is_active = TRUE
                      AND r.list_json IS NOT NULL
                      AND r.detail_json IS NOT NULL
                      AND r.detail_status = 'COMPLETED'
                      AND (%s::boolean = TRUE OR cp.condition_profile_id IS NULL)
                    ORDER BY p.policy_id
                    LIMIT %s
                """,
                (overwrite, limit),
            )
            rows = await cur.fetchall()

        columns = [
            "policy_id",
            "policy_code",
            "policy_name",
            "main_category",
            "sub_category",
            "provider_name",
            "benefit_type",
            "application_status",
            "official_url",
            "easy_summary",
            "target_description",
            "benefit_description",
            "application_method",
            "application_period_text",
            "caution",
            "life_array",
            "target_individual_array",
            "interest_theme_array",
            "raw_target_detail",
            "raw_selection_criteria",
            "raw_outline",
            "raw_benefit_content",
        ]
        return [dict(zip(columns, row, strict=True)) for row in rows]

    @staticmethod
    async def upsert_profile(
        conn,
        *,
        policy_id: int,
        condition_json: dict[str, Any],
        target_summary: str | None,
        confidence: float | None,
        review_required: bool,
        quality_flags: list[Any],
        source_text: str,
        source_fields: list[str],
    ) -> int:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    INSERT INTO policy_condition_profile (
                        policy_id,
                        condition_json,
                        target_summary,
                        confidence,
                        review_required,
                        quality_flags,
                        source_text,
                        source_fields,
                        extracted_at,
                        updated_at
                    )
                    VALUES (
                        %s,
                        %s::jsonb,
                        %s,
                        %s,
                        %s,
                        %s::jsonb,
                        %s,
                        %s::jsonb,
                        CURRENT_TIMESTAMP,
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (policy_id) DO UPDATE SET
                        condition_json = EXCLUDED.condition_json,
                        target_summary = EXCLUDED.target_summary,
                        confidence = EXCLUDED.confidence,
                        review_required = EXCLUDED.review_required,
                        quality_flags = EXCLUDED.quality_flags,
                        source_text = EXCLUDED.source_text,
                        source_fields = EXCLUDED.source_fields,
                        extracted_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING condition_profile_id
                """,
                (
                    policy_id,
                    json.dumps(condition_json, ensure_ascii=False),
                    target_summary,
                    confidence,
                    review_required,
                    json.dumps(quality_flags, ensure_ascii=False),
                    source_text,
                    json.dumps(source_fields, ensure_ascii=False),
                ),
            )
            row = await cur.fetchone()
        return int(row[0])

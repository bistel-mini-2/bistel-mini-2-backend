from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


class CompareRepository:
    @classmethod
    async def find_policies_by_slugs(
        cls,
        db: AsyncSession,
        slugs: list[str],
    ) -> list[dict[str, Any]]:
        if not slugs:
            return []

        statement = text(
            """
            SELECT
                p.policy_id,
                p.policy_code AS slug,
                p.policy_name AS name,
                p.main_category AS category,
                p.sub_category,
                p.provider_name AS agency,
                p.benefit_type,
                p.application_status,
                p.application_start_date,
                p.application_end_date,
                p.region_scope,
                p.region_code,
                p.contact,
                p.official_url,
                pd.easy_summary,
                pd.target_description,
                pd.benefit_description,
                pd.application_method,
                pd.application_period_text,
                pd.caution,
                COALESCE(
                    (
                        SELECT jsonb_agg(pt.tag_name ORDER BY pt.tag_name)
                        FROM policy_tag pt
                        WHERE pt.policy_id = p.policy_id
                    ),
                    '[]'::jsonb
                ) AS tags,
                COALESCE(
                    (
                        SELECT jsonb_agg(rd.document_name ORDER BY rd.document_name)
                        FROM required_document rd
                        WHERE rd.policy_id = p.policy_id
                    ),
                    '[]'::jsonb
                ) AS required_documents
            FROM policy p
            LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
            WHERE p.is_active = TRUE
              AND p.policy_code IN :slugs
            """
        ).bindparams(bindparam("slugs", expanding=True))
        result = await db.execute(statement, {"slugs": slugs})
        return [dict(row) for row in result.mappings().all()]

    @classmethod
    async def find_related_policies(
        cls,
        db: AsyncSession,
        *,
        excluded_policy_ids: list[int],
        category: str | None,
        tags: list[str],
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        tag_filters = [
            f"LOWER(pt.tag_name) = LOWER(:tag_{index})"
            for index, _ in enumerate(tags[:5])
        ]
        params: dict[str, Any] = {
            "excluded_policy_ids": excluded_policy_ids,
            "category": category,
            "limit": limit,
        }
        params.update({f"tag_{index}": tag for index, tag in enumerate(tags[:5])})

        related_score_sql = "0"
        if category:
            related_score_sql += (
                " + CASE WHEN LOWER(p.main_category) = LOWER(:category) "
                "THEN 5 ELSE 0 END"
            )
        if tag_filters:
            related_score_sql += (
                " + COALESCE((SELECT COUNT(*) FROM policy_tag pt "
                "WHERE pt.policy_id = p.policy_id "
                f"AND ({' OR '.join(tag_filters)})), 0)"
            )

        statement = text(
            f"""
            SELECT
                p.policy_id,
                p.policy_code AS slug,
                p.policy_name AS name,
                ({related_score_sql}) AS related_score
            FROM policy p
            WHERE p.is_active = TRUE
              AND p.policy_id NOT IN :excluded_policy_ids
            ORDER BY related_score DESC, p.updated_at DESC, p.policy_id DESC
            LIMIT :limit
            """
        ).bindparams(bindparam("excluded_policy_ids", expanding=True))
        result = await db.execute(statement, params)
        return [dict(row) for row in result.mappings().all()]

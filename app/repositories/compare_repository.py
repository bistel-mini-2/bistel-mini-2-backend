from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


class CompareRepository:
    @classmethod
    async def ensure_compare_history_schema(cls, db: AsyncSession) -> None:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS compare_history (
                    compare_history_id bigserial PRIMARY KEY,
                    user_id bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    policy_a_id bigint REFERENCES policy(policy_id) ON DELETE SET NULL,
                    policy_b_id bigint REFERENCES policy(policy_id) ON DELETE SET NULL,
                    title varchar(255),
                    compared_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at timestamp
                )
                """
            )
        )
        alter_statements = [
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS policy_a_id bigint",
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS policy_b_id bigint",
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS title varchar(255)",
            (
                "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS "
                "compared_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ),
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS deleted_at timestamp",
        ]
        for statement in alter_statements:
            await db.execute(text(statement))
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS compare_history_user_compared_idx
                ON compare_history(user_id, compared_at DESC)
                """
            )
        )

    @classmethod
    async def save_compare_history(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        policy_a_id: int,
        policy_b_id: int,
    ) -> int:
        await cls.ensure_compare_history_schema(db)
        result = await db.execute(
            text(
                """
                INSERT INTO compare_history (
                    user_id,
                    policy_a_id,
                    policy_b_id,
                    compared_at
                )
                VALUES (:user_id, :policy_a_id, :policy_b_id, CURRENT_TIMESTAMP)
                RETURNING compare_history_id
                """
            ),
            {
                "user_id": user_id,
                "policy_a_id": policy_a_id,
                "policy_b_id": policy_b_id,
            },
        )
        return int(result.scalar_one())

    @classmethod
    async def find_compare_history(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        page: int,
        size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        await cls.ensure_compare_history_schema(db)
        count_result = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM compare_history ch
                WHERE ch.user_id = :user_id
                  AND ch.deleted_at IS NULL
                  AND ch.policy_a_id IS NOT NULL
                  AND ch.policy_b_id IS NOT NULL
                """
            ),
            {"user_id": user_id},
        )
        total = int(count_result.scalar_one() or 0)

        result = await db.execute(
            text(
                """
                SELECT
                    ch.compare_history_id AS id,
                    pa.policy_name AS policy_a_name,
                    pb.policy_name AS policy_b_name,
                    pa.policy_code AS policy_a_slug,
                    pb.policy_code AS policy_b_slug,
                    ch.compared_at
                FROM compare_history ch
                JOIN policy pa ON pa.policy_id = ch.policy_a_id
                JOIN policy pb ON pb.policy_id = ch.policy_b_id
                WHERE ch.user_id = :user_id
                  AND ch.deleted_at IS NULL
                  AND ch.policy_a_id IS NOT NULL
                  AND ch.policy_b_id IS NOT NULL
                ORDER BY ch.compared_at DESC, ch.compare_history_id DESC
                LIMIT :limit OFFSET :offset
                """
            ),
            {
                "user_id": user_id,
                "limit": size,
                "offset": (page - 1) * size,
            },
        )
        return [dict(row) for row in result.mappings().all()], total

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

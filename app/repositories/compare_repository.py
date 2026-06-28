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
                    title varchar(255),
                    compared_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    deleted_at timestamp
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS compare_history_item (
                    compare_history_item_id bigserial PRIMARY KEY,
                    compare_history_id bigint NOT NULL
                        REFERENCES compare_history(compare_history_id)
                        ON DELETE CASCADE,
                    policy_id bigint NOT NULL REFERENCES policy(policy_id)
                        ON DELETE CASCADE,
                    added_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        alter_statements = [
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS title varchar(255)",
            (
                "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS "
                "compared_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ),
            "ALTER TABLE compare_history ADD COLUMN IF NOT EXISTS deleted_at timestamp",
            (
                "ALTER TABLE compare_history_item ADD COLUMN IF NOT EXISTS "
                "added_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ),
        ]
        for statement in alter_statements:
            await db.execute(text(statement))
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS compare_history_user_id_compared_at_idx
                ON compare_history(user_id, compared_at DESC)
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                compare_history_item_compare_history_id_policy_id_idx
                ON compare_history_item(compare_history_id, policy_id)
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
                    compared_at
                )
                VALUES (:user_id, CURRENT_TIMESTAMP)
                RETURNING compare_history_id
                """
            ),
            {"user_id": user_id},
        )
        compare_history_id = int(result.scalar_one())
        await db.execute(
            text(
                """
                INSERT INTO compare_history_item (
                    compare_history_id,
                    policy_id,
                    added_at
                )
                VALUES
                    (:compare_history_id, :policy_a_id, CURRENT_TIMESTAMP),
                    (:compare_history_id, :policy_b_id, CURRENT_TIMESTAMP)
                ON CONFLICT (compare_history_id, policy_id) DO NOTHING
                """
            ),
            {
                "compare_history_id": compare_history_id,
                "policy_a_id": policy_a_id,
                "policy_b_id": policy_b_id,
            },
        )
        return compare_history_id

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
                FROM (
                    SELECT ch.compare_history_id
                    FROM compare_history ch
                    JOIN compare_history_item chi
                      ON chi.compare_history_id = ch.compare_history_id
                    WHERE ch.user_id = :user_id
                      AND ch.deleted_at IS NULL
                    GROUP BY ch.compare_history_id
                    HAVING COUNT(*) = 2
                ) counted
                """
            ),
            {"user_id": user_id},
        )
        total = int(count_result.scalar_one() or 0)

        result = await db.execute(
            text(
                """
                WITH ordered_history AS (
                    SELECT
                        ch.compare_history_id,
                        ch.compared_at,
                        array_agg(p.policy_name ORDER BY chi.added_at, chi.compare_history_item_id) AS policy_names,
                        array_agg(p.policy_code ORDER BY chi.added_at, chi.compare_history_item_id) AS policy_slugs
                    FROM compare_history ch
                    JOIN compare_history_item chi
                      ON chi.compare_history_id = ch.compare_history_id
                    JOIN policy p ON p.policy_id = chi.policy_id
                    WHERE ch.user_id = :user_id
                      AND ch.deleted_at IS NULL
                    GROUP BY ch.compare_history_id, ch.compared_at
                    HAVING COUNT(*) = 2
                )
                SELECT
                    compare_history_id AS id,
                    policy_names[1] AS policy_a_name,
                    policy_names[2] AS policy_b_name,
                    policy_slugs[1] AS policy_a_slug,
                    policy_slugs[2] AS policy_b_slug,
                    compared_at
                FROM ordered_history
                ORDER BY compared_at DESC, compare_history_id DESC
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
    async def soft_delete_compare_history(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        history_id: int,
    ) -> bool:
        await cls.ensure_compare_history_schema(db)
        result = await db.execute(
            text(
                """
                UPDATE compare_history
                SET deleted_at = CURRENT_TIMESTAMP
                WHERE compare_history_id = :history_id
                  AND user_id = :user_id
                  AND deleted_at IS NULL
                RETURNING compare_history_id
                """
            ),
            {
                "history_id": history_id,
                "user_id": user_id,
            },
        )
        return result.scalar_one_or_none() is not None

    @classmethod
    async def soft_delete_all_compare_history(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
    ) -> int:
        await cls.ensure_compare_history_schema(db)
        result = await db.execute(
            text(
                """
                UPDATE compare_history
                SET deleted_at = CURRENT_TIMESTAMP
                WHERE user_id = :user_id
                  AND deleted_at IS NULL
                RETURNING compare_history_id
                """
            ),
            {"user_id": user_id},
        )
        return len(result.scalars().all())

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
                cp.condition_profile_id,
                cp.condition_json AS condition_profile_json,
                cp.target_summary AS condition_profile_target_summary,
                cp.source_text AS condition_profile_source_text,
                cp.confidence AS condition_profile_confidence,
                cp.review_required AS condition_profile_review_required,
                cp.quality_flags AS condition_profile_quality_flags,
                cp.source_fields AS condition_profile_source_fields,
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
            JOIN policy_condition_profile cp ON cp.policy_id = p.policy_id
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

import json
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession


class RecommendationCandidateRepository:
    @staticmethod
    async def ensure_candidate_schema(db: AsyncSession) -> None:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS recommendation_candidate (
                    candidate_id bigserial PRIMARY KEY,
                    request_id bigint NOT NULL
                        REFERENCES recommendation_request(request_id) ON DELETE CASCADE,
                    policy_id bigint NOT NULL
                        REFERENCES policy(policy_id) ON DELETE CASCADE,
                    filter_match_json jsonb,
                    retrieval_score numeric(10, 4),
                    rerank_score numeric(10, 4),
                    candidate_status varchar(50) NOT NULL DEFAULT 'CANDIDATE',
                    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        for statement in [
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            recommendation_candidate_request_policy_uidx
            ON recommendation_candidate (request_id, policy_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS
            recommendation_candidate_request_id_idx
            ON recommendation_candidate (request_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS
            recommendation_candidate_policy_id_idx
            ON recommendation_candidate (policy_id)
            """,
        ]:
            await db.execute(text(statement))

    async def find_policy_rows(
        self,
        db: AsyncSession,
        query_terms: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        conditions = ["p.is_active = TRUE"]
        params: dict[str, Any] = {"limit": limit}
        if query_terms:
            term_conditions: list[str] = []
            for index, term in enumerate(query_terms):
                param_name = f"term_{index}"
                params[param_name] = f"%{term}%"
                term_conditions.append(
                    f"""
                    (
                        p.policy_name ILIKE :{param_name}
                        OR p.main_category ILIKE :{param_name}
                        OR p.sub_category ILIKE :{param_name}
                        OR p.provider_name ILIKE :{param_name}
                        OR p.benefit_type ILIKE :{param_name}
                        OR pd.easy_summary ILIKE :{param_name}
                        OR pd.target_description ILIKE :{param_name}
                        OR pd.benefit_description ILIKE :{param_name}
                        OR pd.caution ILIKE :{param_name}
                        OR EXISTS (
                            SELECT 1
                            FROM policy_tag pt
                            WHERE pt.policy_id = p.policy_id
                              AND pt.tag_name ILIKE :{param_name}
                        )
                    )
                    """
                )
            conditions.append(f"({' OR '.join(term_conditions)})")

        result = await db.execute(
            text(
                f"""
                SELECT
                    p.policy_id,
                    p.policy_code,
                    p.policy_name,
                    p.main_category,
                    p.sub_category,
                    p.provider_name,
                    p.region_scope,
                    p.region_code,
                    p.benefit_type,
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
                    ) AS tags
                FROM policy p
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                WHERE {' AND '.join(conditions)}
                ORDER BY p.policy_id
                LIMIT :limit
                """
            ),
            params,
        )
        return [dict(row) for row in result.mappings().all()]

    async def find_policy_rows_by_ids(
        self,
        db: AsyncSession,
        policy_ids: list[int],
    ) -> list[dict[str, Any]]:
        if not policy_ids:
            return []

        statement = text(
            """
            SELECT
                p.policy_id,
                p.policy_code,
                p.policy_name,
                p.main_category,
                p.sub_category,
                p.provider_name,
                p.region_scope,
                p.region_code,
                p.benefit_type,
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
                ) AS tags
            FROM policy p
            LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
            WHERE p.is_active = TRUE
              AND p.policy_id IN :policy_ids
            ORDER BY p.policy_id
            """
        ).bindparams(bindparam("policy_ids", expanding=True))
        result = await db.execute(
            statement,
            {"policy_ids": list(dict.fromkeys(policy_ids))},
        )
        return [dict(row) for row in result.mappings().all()]

    async def replace_candidates(
        self,
        db: AsyncSession,
        request_id: int,
        candidates: list[Any],
    ) -> None:
        await self.ensure_candidate_schema(db)
        await db.execute(
            text("DELETE FROM recommendation_candidate WHERE request_id = :request_id"),
            {"request_id": request_id},
        )
        for candidate in candidates:
            await db.execute(
                text(
                    """
                    INSERT INTO recommendation_candidate (
                        request_id,
                        policy_id,
                        filter_match_json,
                        retrieval_score,
                        rerank_score,
                        candidate_status
                    )
                    VALUES (
                        :request_id,
                        :policy_id,
                        CAST(:filter_match_json AS jsonb),
                        :retrieval_score,
                        NULL,
                        :candidate_status
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "policy_id": candidate.policy.policy_id,
                    "filter_match_json": json.dumps(
                        candidate.filter_match_json,
                        ensure_ascii=False,
                    ),
                    "retrieval_score": candidate.retrieval_score,
                    "candidate_status": candidate.candidate_status,
                },
            )

    async def policy_rule_table_exists(self, db: AsyncSession) -> bool:
        result = await db.execute(
            text("SELECT to_regclass('public.policy_rule') IS NOT NULL")
        )
        return bool(result.scalar_one())

    async def find_policy_rules(
        self,
        db: AsyncSession,
        policy_ids: list[int],
    ) -> dict[int, list[dict[str, Any]]]:
        if not policy_ids or not await self.policy_rule_table_exists(db):
            return {}

        statement = text(
            """
            SELECT
                rule_id,
                policy_id,
                rule_type,
                operator,
                field_name,
                value_json,
                is_hard_filter,
                manual_check_required,
                manual_check_reason,
                note
            FROM policy_rule
            WHERE policy_id IN :policy_ids
            """
        ).bindparams(bindparam("policy_ids", expanding=True))
        result = await db.execute(statement, {"policy_ids": policy_ids})

        rules_by_policy: dict[int, list[dict[str, Any]]] = {}
        for row in result.mappings().all():
            policy_id = int(row["policy_id"])
            rules_by_policy.setdefault(policy_id, []).append(dict(row))
        return rules_by_policy

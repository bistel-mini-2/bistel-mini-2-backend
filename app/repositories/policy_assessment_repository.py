import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_contract import AssessmentResult, EvidenceChunk


ASSESSMENT_EVIDENCE_ROLES = {
    "SUMMARY",
    "TARGET",
    "BENEFIT",
    "APPLICATION",
    "CAUTION",
}


class PolicyAssessmentRepository:
    @staticmethod
    async def ensure_assessment_schema(db: AsyncSession) -> None:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS policy_assessment (
                    assessment_id bigserial PRIMARY KEY,
                    request_id bigint,
                    recommendation_request_id bigint,
                    eligibility_request_id bigint,
                    policy_id bigint NOT NULL REFERENCES policy(policy_id) ON DELETE CASCADE,
                    assessment_type varchar(50) NOT NULL,
                    assessment_status varchar(50) NOT NULL,
                    confidence_score numeric(5, 2),
                    matched_conditions_json jsonb,
                    missing_conditions_json jsonb,
                    conflicting_conditions_json jsonb,
                    manual_check_points_json jsonb,
                    reason_summary text,
                    selected_for_result boolean NOT NULL DEFAULT false,
                    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        for statement in [
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS request_id bigint",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS recommendation_request_id bigint",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS eligibility_request_id bigint",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS policy_id bigint",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS assessment_type varchar(50)",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS assessment_status varchar(50)",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS confidence_score numeric(5, 2)",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS matched_conditions_json jsonb",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS missing_conditions_json jsonb",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS conflicting_conditions_json jsonb",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS manual_check_points_json jsonb",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS reason_summary text",
            "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS selected_for_result boolean NOT NULL DEFAULT false",
        ]:
            await db.execute(text(statement))

        for statement in [
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            policy_assessment_recommendation_policy_type_uidx
            ON policy_assessment (recommendation_request_id, policy_id, assessment_type)
            WHERE recommendation_request_id IS NOT NULL
            """,
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            policy_assessment_request_policy_type_uidx
            ON policy_assessment (request_id, policy_id, assessment_type)
            WHERE request_id IS NOT NULL
            """,
        ]:
            await db.execute(text(statement))

    async def replace_recommendation_assessments(
        self,
        db: AsyncSession,
        request_id: int,
        assessments: list[Any],
    ) -> None:
        await self.ensure_assessment_schema(db)
        await db.execute(
            text(
                """
                DELETE FROM policy_assessment
                WHERE (request_id = :request_id OR recommendation_request_id = :request_id)
                  AND assessment_type = 'recommendation_assessment'
                """
            ),
            {"request_id": request_id},
        )
        for assessment in assessments:
            await db.execute(
                text(
                    """
                    INSERT INTO policy_assessment (
                        request_id,
                        recommendation_request_id,
                        eligibility_request_id,
                        policy_id,
                        assessment_type,
                        assessment_status,
                        confidence_score,
                        matched_conditions_json,
                        missing_conditions_json,
                        conflicting_conditions_json,
                        manual_check_points_json,
                        reason_summary,
                        selected_for_result
                    )
                    VALUES (
                        :request_id,
                        :recommendation_request_id,
                        NULL,
                        :policy_id,
                        'recommendation_assessment',
                        :assessment_status,
                        :confidence_score,
                        CAST(:matched_conditions_json AS jsonb),
                        CAST(:missing_conditions_json AS jsonb),
                        CAST(:conflicting_conditions_json AS jsonb),
                        CAST(:manual_check_points_json AS jsonb),
                        :reason_summary,
                        :selected_for_result
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "recommendation_request_id": request_id,
                    "policy_id": assessment.policy_id,
                    "assessment_status": assessment.assessment_status.value,
                    "confidence_score": assessment.confidence_score,
                    "matched_conditions_json": self._json(
                        assessment.matched_conditions_json
                    ),
                    "missing_conditions_json": self._json(
                        assessment.missing_conditions_json
                    ),
                    "conflicting_conditions_json": self._json(
                        assessment.conflicting_conditions_json
                    ),
                    "manual_check_points_json": self._json(
                        assessment.manual_check_points_json
                    ),
                    "reason_summary": assessment.reason_summary,
                    "selected_for_result": assessment.selected_for_result,
                },
            )

    @staticmethod
    async def ensure_policy_assessment_schema(conn) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS policy_assessment (
                        assessment_id bigserial PRIMARY KEY,
                        request_id bigint,
                        recommendation_request_id bigint,
                        eligibility_request_id bigint,
                        policy_id bigint NOT NULL REFERENCES policy(policy_id) ON DELETE CASCADE,
                        assessment_type varchar(50) NOT NULL,
                        assessment_status varchar(50) NOT NULL,
                        confidence_score numeric(5, 2),
                        matched_conditions_json jsonb,
                        missing_conditions_json jsonb,
                        conflicting_conditions_json jsonb,
                        manual_check_points_json jsonb,
                        reason_summary text,
                        selected_for_result boolean NOT NULL DEFAULT false,
                        created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """
            )
            await cur.execute(
                """
                    CREATE TABLE IF NOT EXISTS assessment_evidence (
                        evidence_id bigserial PRIMARY KEY,
                        assessment_id bigint NOT NULL
                            REFERENCES policy_assessment(assessment_id) ON DELETE CASCADE,
                        chunk_id bigint NOT NULL
                            REFERENCES policy_document_chunk(chunk_id) ON DELETE CASCADE,
                        snippet text,
                        similarity_score numeric(10, 6),
                        evidence_role varchar(50),
                        created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                """
            )
            await cur.execute(
                "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS recommendation_request_id bigint"
            )
            await cur.execute(
                "ALTER TABLE policy_assessment ADD COLUMN IF NOT EXISTS eligibility_request_id bigint"
            )
            await cur.execute(
                "ALTER TABLE policy_assessment ALTER COLUMN request_id DROP NOT NULL"
            )
            await cur.execute(
                """
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    policy_assessment_request_id_policy_id_assessment_type_idx
                    ON policy_assessment (request_id, policy_id, assessment_type)
                """
            )

    @staticmethod
    async def save_assessment(
        conn,
        result: AssessmentResult,
        assessment_type: str,
        recommendation_request_id: int | None = None,
        eligibility_request_id: int | None = None,
        confidence_score: float | None = None,
        selected_for_result: bool = False,
    ) -> int:
        await PolicyAssessmentRepository.ensure_policy_assessment_schema(conn)
        request_id = recommendation_request_id

        async with conn.cursor() as cur:
            await cur.execute(
                """
                    INSERT INTO policy_assessment (
                        request_id,
                        recommendation_request_id,
                        eligibility_request_id,
                        policy_id,
                        assessment_type,
                        assessment_status,
                        confidence_score,
                        matched_conditions_json,
                        missing_conditions_json,
                        conflicting_conditions_json,
                        manual_check_points_json,
                        reason_summary,
                        selected_for_result
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s
                    )
                    RETURNING assessment_id
                """,
                (
                    request_id,
                    recommendation_request_id,
                    eligibility_request_id,
                    int(result.policy_id),
                    assessment_type,
                    result.assessment_status.value,
                    confidence_score,
                    PolicyAssessmentRepository._json(result.matched_conditions),
                    PolicyAssessmentRepository._json(result.missing_conditions),
                    PolicyAssessmentRepository._json(result.conflicting_conditions),
                    PolicyAssessmentRepository._json(result.manual_check_points),
                    result.reason_summary,
                    selected_for_result,
                ),
            )
            row = await cur.fetchone()
            assessment_id = int(row[0])
            await PolicyAssessmentRepository.replace_evidences(
                cur=cur,
                assessment_id=assessment_id,
                evidences=result.evidences,
            )
        return assessment_id

    @staticmethod
    async def replace_evidences(
        cur,
        assessment_id: int,
        evidences: list[EvidenceChunk],
    ) -> None:
        await cur.execute(
            "DELETE FROM assessment_evidence WHERE assessment_id = %s",
            (assessment_id,),
        )
        for evidence in evidences:
            await cur.execute(
                """
                    INSERT INTO assessment_evidence (
                        assessment_id,
                        chunk_id,
                        snippet,
                        similarity_score,
                        evidence_role
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    assessment_id,
                    int(evidence.chunk_id),
                    evidence.snippet,
                    evidence.score,
                    PolicyAssessmentRepository._normalize_evidence_role(
                        evidence.evidence_role
                    ),
                ),
            )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _normalize_evidence_role(evidence_role: str | None) -> str | None:
        if evidence_role is None:
            return None

        normalized_role = evidence_role.strip().upper()
        if not normalized_role:
            return None
        if normalized_role not in ASSESSMENT_EVIDENCE_ROLES:
            return None
        return normalized_role

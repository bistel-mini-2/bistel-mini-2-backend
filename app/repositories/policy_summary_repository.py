from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ai_contract import RequestStatus


class PolicySummaryRepository:
    @staticmethod
    async def ensure_schema(db: AsyncSession) -> None:
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS policy_summary_cache (
                    summary_id bigserial PRIMARY KEY,
                    policy_id bigint NOT NULL UNIQUE
                        REFERENCES policy(policy_id) ON DELETE CASCADE,
                    condition_profile_id bigint
                        REFERENCES policy_condition_profile(condition_profile_id)
                        ON DELETE SET NULL,
                    condition_profile_updated_at timestamp,
                    summary_source varchar(50) NOT NULL
                        DEFAULT 'policy_condition_profile',
                    request_status varchar(50) NOT NULL DEFAULT 'PROCESSING',
                    summary text,
                    evidence_json jsonb,
                    error_message text,
                    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await db.execute(
            text(
                """
                ALTER TABLE policy_summary_cache
                    ADD COLUMN IF NOT EXISTS condition_profile_id bigint
                        REFERENCES policy_condition_profile(condition_profile_id)
                        ON DELETE SET NULL,
                    ADD COLUMN IF NOT EXISTS condition_profile_updated_at timestamp,
                    ADD COLUMN IF NOT EXISTS summary_source varchar(50)
                        NOT NULL DEFAULT 'policy_condition_profile'
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS policy_summary_cache_policy_id_idx
                ON policy_summary_cache (policy_id)
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS
                    policy_summary_cache_condition_profile_id_idx
                ON policy_summary_cache (condition_profile_id)
                """
            )
        )

    async def find_by_policy_id(
        self,
        db: AsyncSession,
        policy_id: int,
    ) -> dict[str, Any] | None:
        await self.ensure_schema(db)
        result = await db.execute(
            text(
                """
                SELECT summary_id, policy_id, condition_profile_id,
                       condition_profile_updated_at, summary_source,
                       request_status, summary, evidence_json, error_message,
                       updated_at
                FROM policy_summary_cache
                WHERE policy_id = :policy_id
                """
            ),
            {"policy_id": policy_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def create_processing_if_absent(
        self,
        db: AsyncSession,
        policy_id: int,
        *,
        force_refresh: bool = False,
        stale_after_minutes: int = 10,
    ) -> tuple[dict[str, Any], bool]:
        await self.ensure_schema(db)
        profile_meta = await self.find_condition_profile_meta(db, policy_id)
        if force_refresh:
            refreshed = await self.reset_processing(
                db,
                policy_id,
                profile_meta=profile_meta,
            )
            if refreshed is not None:
                return refreshed, True

        result = await db.execute(
            text(
                """
                INSERT INTO policy_summary_cache (
                    policy_id,
                    condition_profile_id,
                    condition_profile_updated_at,
                    summary_source,
                    request_status
                )
                VALUES (
                    :policy_id,
                    :condition_profile_id,
                    :condition_profile_updated_at,
                    :summary_source,
                    :request_status
                )
                ON CONFLICT (policy_id) DO NOTHING
                RETURNING summary_id, policy_id, condition_profile_id,
                          condition_profile_updated_at, summary_source,
                          request_status, summary, evidence_json,
                          error_message, updated_at
                """
            ),
            {
                "policy_id": policy_id,
                "condition_profile_id": profile_meta.get("condition_profile_id"),
                "condition_profile_updated_at": profile_meta.get("updated_at"),
                "summary_source": "policy_condition_profile",
                "request_status": RequestStatus.PROCESSING.value,
            },
        )
        inserted = result.mappings().one_or_none()
        if inserted is not None:
            return dict(inserted), True

        existing = await self.find_by_policy_id(db, policy_id)
        if existing is None:
            raise RuntimeError("policy summary cache insert failed")
        if self._should_restart(existing, stale_after_minutes) or self._profile_changed(
            existing,
            profile_meta,
        ):
            restarted = await self.reset_processing(
                db,
                policy_id,
                profile_meta=profile_meta,
            )
            if restarted is not None:
                return restarted, True
        return existing, False

    async def find_condition_profile_meta(
        self,
        db: AsyncSession,
        policy_id: int,
    ) -> dict[str, Any]:
        result = await db.execute(
            text(
                """
                SELECT condition_profile_id, updated_at
                FROM policy_condition_profile
                WHERE policy_id = :policy_id
                """
            ),
            {"policy_id": policy_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else {}

    async def reset_processing(
        self,
        db: AsyncSession,
        policy_id: int,
        *,
        profile_meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        profile_meta = profile_meta or {}
        result = await db.execute(
            text(
                """
                UPDATE policy_summary_cache
                SET request_status = :request_status,
                    condition_profile_id = :condition_profile_id,
                    condition_profile_updated_at = :condition_profile_updated_at,
                    summary_source = :summary_source,
                    summary = NULL,
                    evidence_json = NULL,
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE policy_id = :policy_id
                RETURNING summary_id, policy_id, condition_profile_id,
                          condition_profile_updated_at, summary_source,
                          request_status, summary, evidence_json,
                          error_message, updated_at
                """
            ),
            {
                "policy_id": policy_id,
                "condition_profile_id": profile_meta.get("condition_profile_id"),
                "condition_profile_updated_at": profile_meta.get("updated_at"),
                "summary_source": "policy_condition_profile",
                "request_status": RequestStatus.PROCESSING.value,
            },
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def find_generation_context(
        self,
        db: AsyncSession,
        summary_id: int,
    ) -> dict[str, Any] | None:
        await self.ensure_schema(db)
        result = await db.execute(
            text(
                """
                SELECT
                    c.summary_id,
                    c.request_status,
                    c.condition_profile_id AS cache_condition_profile_id,
                    c.condition_profile_updated_at
                        AS cache_condition_profile_updated_at,
                    c.summary_source,
                    p.policy_id,
                    p.policy_code AS slug,
                    p.policy_name AS name,
                    p.main_category AS category,
                    p.provider_name AS agency,
                    p.benefit_type,
                    p.application_status,
                    p.region_scope,
                    p.region_code,
                    p.official_url,
                    p.contact,
                    pd.easy_summary,
                    pd.target_description,
                    pd.benefit_description,
                    pd.application_method,
                    pd.application_period_text,
                    pd.caution,
                    cp.condition_profile_id,
                    cp.condition_json AS condition_profile_json,
                    cp.target_summary AS condition_profile_target_summary,
                    cp.confidence AS condition_profile_confidence,
                    cp.review_required AS condition_profile_review_required,
                    cp.quality_flags AS condition_profile_quality_flags,
                    cp.source_text AS condition_profile_source_text,
                    cp.source_fields AS condition_profile_source_fields,
                    cp.updated_at AS condition_profile_updated_at
                FROM policy_summary_cache c
                JOIN policy p ON p.policy_id = c.policy_id
                LEFT JOIN policy_detail pd ON pd.policy_id = p.policy_id
                LEFT JOIN policy_condition_profile cp
                    ON cp.policy_id = p.policy_id
                WHERE c.summary_id = :summary_id
                  AND p.is_active = TRUE
                """
            ),
            {"summary_id": summary_id},
        )
        row = result.mappings().one_or_none()
        return dict(row) if row is not None else None

    async def mark_completed(
        self,
        db: AsyncSession,
        summary_id: int,
        summary: str,
        evidence: list[str],
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE policy_summary_cache
                SET request_status = :request_status,
                    summary = :summary,
                    evidence_json = CAST(:evidence_json AS jsonb),
                    error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE summary_id = :summary_id
                """
            ),
            {
                "summary_id": summary_id,
                "request_status": RequestStatus.COMPLETED.value,
                "summary": summary,
                "evidence_json": self._json_dumps(evidence),
            },
        )

    async def mark_failed(
        self,
        db: AsyncSession,
        summary_id: int,
        error_message: str,
    ) -> None:
        await db.execute(
            text(
                """
                UPDATE policy_summary_cache
                SET request_status = :request_status,
                    error_message = :error_message,
                    updated_at = CURRENT_TIMESTAMP
                WHERE summary_id = :summary_id
                """
            ),
            {
                "summary_id": summary_id,
                "request_status": RequestStatus.FAILED.value,
                "error_message": error_message,
            },
        )

    @staticmethod
    def _json_dumps(value: Any) -> str:
        import json

        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _should_restart(cache: dict[str, Any], stale_after_minutes: int) -> bool:
        status = str(cache.get("request_status") or "")
        if status == RequestStatus.FAILED.value:
            return True
        if status != RequestStatus.PROCESSING.value:
            return False

        updated_at = cache.get("updated_at")
        if updated_at is None:
            return False

        from datetime import datetime, timedelta, timezone

        now = datetime.now(tz=updated_at.tzinfo or timezone.utc)
        comparable_updated_at = updated_at
        if updated_at.tzinfo is None:
            comparable_updated_at = updated_at.replace(tzinfo=timezone.utc)
        return now - comparable_updated_at > timedelta(minutes=stale_after_minutes)

    @staticmethod
    def _profile_changed(
        cache: dict[str, Any],
        profile_meta: dict[str, Any],
    ) -> bool:
        current_profile_id = profile_meta.get("condition_profile_id")
        if cache.get("condition_profile_id") != current_profile_id:
            return True
        return (
            current_profile_id is not None
            and cache.get("condition_profile_updated_at")
            != profile_meta.get("updated_at")
        )

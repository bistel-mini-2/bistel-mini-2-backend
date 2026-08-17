from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.eligibility_request import EligibilityRequest
from app.db.models.recommendation_request import RecommendationRequest
from app.repositories.user_repository import UserRepository
from app.schemas.ai_contract import RequestStatus


AiRequestModel = RecommendationRequest | EligibilityRequest
STALE_AI_REQUEST_MINUTES = 5


class AiRequestRepository:
    REQUEST_MODELS = {
        "recommendation": RecommendationRequest,
        "eligibility": EligibilityRequest,
    }

    @staticmethod
    async def ensure_request_schema(db: AsyncSession) -> None:
        await UserRepository.ensure_user_schema(db)
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS recommendation_request (
                    request_id bigserial PRIMARY KEY,
                    user_id bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    source_type varchar(30) NOT NULL DEFAULT 'FORM',
                    source_ref_id varchar(100),
                    idempotency_key varchar(120),
                    raw_query text,
                    parsed_query_json jsonb,
                    merged_condition_json jsonb,
                    profile_conflict_json jsonb,
                    result_json jsonb,
                    error_message text,
                    request_status varchar(50) NOT NULL DEFAULT 'READY',
                    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        await db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS eligibility_request (
                    request_id bigserial PRIMARY KEY,
                    user_id bigint NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    policy_id bigint NOT NULL REFERENCES policy(policy_id) ON DELETE CASCADE,
                    source_type varchar(30) NOT NULL DEFAULT 'POLICY_DETAIL',
                    source_ref_id varchar(100),
                    idempotency_key varchar(120),
                    raw_query text,
                    parsed_query_json jsonb,
                    merged_condition_json jsonb,
                    profile_conflict_json jsonb,
                    result_json jsonb,
                    error_message text,
                    request_status varchar(50) NOT NULL DEFAULT 'READY',
                    created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        for statement in [
            "CREATE INDEX IF NOT EXISTS recommendation_request_user_id_idx ON recommendation_request (user_id)",
            "CREATE INDEX IF NOT EXISTS eligibility_request_user_id_idx ON eligibility_request (user_id)",
            "CREATE INDEX IF NOT EXISTS eligibility_request_policy_id_idx ON eligibility_request (policy_id)",
            "ALTER TABLE recommendation_request ADD COLUMN IF NOT EXISTS error_message text",
            "ALTER TABLE recommendation_request ADD COLUMN IF NOT EXISTS result_json jsonb",
            "ALTER TABLE eligibility_request ADD COLUMN IF NOT EXISTS result_json jsonb",
            "ALTER TABLE eligibility_request ADD COLUMN IF NOT EXISTS error_message text",
            "ALTER TABLE recommendation_request ADD COLUMN IF NOT EXISTS source_ref_id varchar(100)",
            "ALTER TABLE eligibility_request ADD COLUMN IF NOT EXISTS source_ref_id varchar(100)",
            "ALTER TABLE recommendation_request ADD COLUMN IF NOT EXISTS idempotency_key varchar(120)",
            "ALTER TABLE eligibility_request ADD COLUMN IF NOT EXISTS idempotency_key varchar(120)",
            "CREATE UNIQUE INDEX IF NOT EXISTS recommendation_request_user_idempotency_key_uidx ON recommendation_request (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
            "CREATE UNIQUE INDEX IF NOT EXISTS eligibility_request_user_idempotency_key_uidx ON eligibility_request (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS recommendation_request_status_updated_at_idx ON recommendation_request (request_status, updated_at)",
            "CREATE INDEX IF NOT EXISTS eligibility_request_status_updated_at_idx ON eligibility_request (request_status, updated_at)",
            "ALTER TABLE recommendation_request ALTER COLUMN raw_query DROP NOT NULL",
            "ALTER TABLE eligibility_request ALTER COLUMN raw_query DROP NOT NULL",
        ]:
            await db.execute(text(statement))

    async def create(
        self,
        db: AsyncSession,
        request_type: str,
        user_id: int,
        source_type: str,
        source_ref_id: str | None = None,
        raw_query: str | None = None,
        selected_conditions: dict[str, Any] | None = None,
        follow_up_resolved: bool = False,
        policy_id: int | None = None,
        idempotency_key: str | None = None,
    ) -> AiRequestModel:
        model = self._model_for(request_type)
        parsed_query_json: dict[str, Any] = {}
        if selected_conditions is not None:
            parsed_query_json["selected_conditions"] = selected_conditions
        if follow_up_resolved:
            parsed_query_json["follow_up_resolved"] = True
        if not parsed_query_json:
            parsed_query_json = None
        values: dict[str, Any] = {
            "user_id": user_id,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "idempotency_key": idempotency_key,
            "raw_query": raw_query,
            "parsed_query_json": parsed_query_json,
            "request_status": RequestStatus.READY.value,
        }
        if model is EligibilityRequest:
            if policy_id is None:
                raise ValueError("policy_id is required for eligibility request")
            values["policy_id"] = policy_id

        request = model(**values)
        db.add(request)
        await db.flush()
        await db.refresh(request)
        return request

    async def find_by_idempotency_key(
        self,
        db: AsyncSession,
        request_type: str,
        user_id: int,
        idempotency_key: str,
    ) -> AiRequestModel | None:
        model = self._model_for(request_type)
        result = await db.execute(
            select(model)
            .where(model.user_id == user_id)
            .where(model.idempotency_key == idempotency_key)
        )
        return result.scalar_one_or_none()

    async def find_by_id(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestModel | None:
        model = self._model_for(request_type)
        result = await db.execute(select(model).where(model.request_id == request_id))
        return result.scalar_one_or_none()

    async def list_completed_by_user(
        self,
        db: AsyncSession,
        request_type: str,
        user_id: int,
        limit: int = 20,
    ) -> list[AiRequestModel]:
        """사용자의 완료된 요청을 최신순으로 조회한다(추천 이력용)."""
        model = self._model_for(request_type)
        result = await db.execute(
            select(model)
            .where(model.user_id == user_id)
            .where(model.request_status == RequestStatus.COMPLETED.value)
            .order_by(model.created_at.desc(), model.request_id.desc())
            .limit(max(1, min(limit, 100)))
        )
        return list(result.scalars().all())

    async def mark_stale_processing_failed(
        self,
        db: AsyncSession,
        request_type: str,
    ) -> int:
        model = self._model_for(request_type)
        cutoff = _utc_now_naive() - timedelta(minutes=STALE_AI_REQUEST_MINUTES)
        result = await db.execute(
            text(
                f"""
                UPDATE {model.__tablename__}
                SET request_status = :failed_status,
                    error_message = :error_message,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_status = :processing_status
                  AND updated_at < :cutoff
                """
            ),
            {
                "failed_status": RequestStatus.FAILED.value,
                "processing_status": RequestStatus.PROCESSING.value,
                "error_message": "서버 재시작 또는 작업 중단으로 처리 상태가 만료되었습니다.",
                "cutoff": cutoff,
            },
        )
        return int(result.rowcount or 0)

    async def update_status(
        self,
        db: AsyncSession,
        request: AiRequestModel,
        status: RequestStatus,
        error_message: str | None = None,
    ) -> AiRequestModel:
        request.request_status = status.value
        request.error_message = error_message
        await db.flush()
        await db.refresh(request)
        return request

    async def update_payload(
        self,
        db: AsyncSession,
        request: AiRequestModel,
        parsed_query_json: dict[str, Any] | None = None,
        merged_condition_json: dict[str, Any] | None = None,
        profile_conflict_json: list[dict[str, Any]] | None = None,
        raw_query: str | None = None,
    ) -> AiRequestModel:
        if parsed_query_json is not None:
            request.parsed_query_json = parsed_query_json
        if merged_condition_json is not None:
            request.merged_condition_json = merged_condition_json
        if profile_conflict_json is not None:
            request.profile_conflict_json = profile_conflict_json
        if raw_query is not None:
            request.raw_query = raw_query
        await db.flush()
        await db.refresh(request)
        return request

    async def update_result(
        self,
        db: AsyncSession,
        request: AiRequestModel,
        result_json: dict[str, Any],
    ) -> AiRequestModel:
        if isinstance(request, (RecommendationRequest, EligibilityRequest)):
            request.result_json = result_json
        await db.flush()
        await db.refresh(request)
        return request

    def _model_for(self, request_type: str):
        try:
            return self.REQUEST_MODELS[request_type]
        except KeyError as exc:
            raise ValueError(f"Unsupported AI request type: {request_type}") from exc


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

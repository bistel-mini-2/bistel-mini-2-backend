import asyncio
import logging
from typing import Annotated

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
from app.common.exceptions import AppException, ErrorCode
from app.repositories.policy_repository import PolicyRepository
from app.repositories.policy_summary_repository import PolicySummaryRepository
from app.schemas.ai_contract import RequestStatus
from app.schemas.policy_summary_schema import PolicySummaryResponse
from app.services.policy_service import PolicyService


POLICY_SUMMARY_STALE_AFTER_MINUTES = 10
POLICY_SUMMARY_TIMEOUT_SECONDS = 60


class PolicySummaryService:
    def __init__(
        self,
        repository: PolicySummaryRepository | None = None,
        graph: PolicySummaryGraphRunner | None = None,
    ) -> None:
        self.repository = repository or PolicySummaryRepository()
        self.graph = graph or PolicySummaryGraphRunner()
        self.logger = logging.getLogger(f"{__name__}.PolicySummaryService")

    async def get_or_start_summary(
        self,
        db: AsyncSession,
        *,
        policy_slug: str,
        force_refresh: bool = False,
    ) -> tuple[PolicySummaryResponse, int | None]:
        normalized_slug = PolicyService._normalize_optional_text(policy_slug)
        if normalized_slug is None:
            raise self._policy_not_found()

        policy = await PolicyRepository.find_policy_detail(
            db,
            policy_slug=normalized_slug,
        )
        if policy is None:
            raise self._policy_not_found()

        cache, created = await self.repository.create_processing_if_absent(
            db,
            int(policy["policy_id"]),
            force_refresh=force_refresh,
            stale_after_minutes=POLICY_SUMMARY_STALE_AFTER_MINUTES,
        )
        response = self._to_response(cache)
        start_summary_id = int(cache["summary_id"]) if created else None
        return response, start_summary_id

    async def process_summary(
        self,
        db: AsyncSession,
        *,
        summary_id: int,
    ) -> None:
        context = await self.repository.find_generation_context(db, summary_id)
        if context is None:
            return
        if context["request_status"] != RequestStatus.PROCESSING.value:
            return

        try:
            result = await asyncio.wait_for(
                self.graph.run(context),
                timeout=POLICY_SUMMARY_TIMEOUT_SECONDS,
            )
            await self.repository.mark_completed(
                db,
                summary_id=summary_id,
                summary=str(result.get("summary") or ""),
                evidence=[
                    str(item)
                    for item in result.get("evidence") or []
                    if item is not None
                ],
            )
        except Exception as exc:
            self.logger.exception(
                "Policy summary generation failed: summary_id=%s",
                summary_id,
            )
            await self.repository.mark_failed(
                db,
                summary_id=summary_id,
                error_message=str(exc),
            )

    def _to_response(self, cache: dict) -> PolicySummaryResponse:
        request_status = RequestStatus(str(cache["request_status"]))
        if request_status in {RequestStatus.READY, RequestStatus.PROCESSING}:
            return PolicySummaryResponse(status="loading")
        if request_status == RequestStatus.COMPLETED:
            return PolicySummaryResponse(
                status="done",
                summary=cache.get("summary"),
                evidence=self._string_list(cache.get("evidence_json")),
            )
        return PolicySummaryResponse(
            status="error",
            summary=None,
            evidence=[],
        )

    @staticmethod
    def _string_list(value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    @staticmethod
    def _policy_not_found() -> AppException:
        return AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.POLICY_NOT_FOUND,
            message="Policy not found",
        )


def get_policy_summary_service() -> PolicySummaryService:
    return PolicySummaryService()


PolicySummaryServiceDep = Annotated[
    PolicySummaryService,
    Depends(get_policy_summary_service),
]

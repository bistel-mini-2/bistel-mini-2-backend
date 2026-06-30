import asyncio
import logging
import re
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
                summary=self._sanitize_summary(result.get("summary")) or "",
                evidence=self._sanitize_evidence(result.get("evidence")),
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
                summary=self._sanitize_summary(cache.get("summary")),
                evidence=self._sanitize_evidence(cache.get("evidence_json")),
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

    @classmethod
    def _sanitize_summary(cls, value) -> str | None:
        if value is None:
            return None
        lines: list[str] = []
        for line in str(value).splitlines():
            clean_line = cls._clean_display_text(line)
            if (
                clean_line
                and not cls._is_internal_or_cutoff_text(clean_line)
                and not cls._is_generic_summary_text(clean_line)
            ):
                lines.append(clean_line)
            if len(lines) >= 3:
                break
        if not lines:
            return None
        return "\n".join(lines)

    @classmethod
    def _sanitize_evidence(cls, value) -> list[str]:
        evidence: list[str] = []
        for item in cls._string_list(value):
            clean_item = cls._clean_display_text(item)
            if (
                clean_item
                and not cls._is_internal_or_cutoff_text(clean_item)
                and not cls._is_generic_evidence_text(clean_item)
                and cls._is_friendly_evidence_text(clean_item)
            ):
                evidence.append(clean_item)
            if len(evidence) >= 3:
                break

        if evidence:
            return evidence

        return cls._default_evidence()

    @staticmethod
    def _default_evidence() -> list[str]:
        return [
            "정책 상세 안내에 있는 대상, 혜택, 신청 정보를 읽기 쉬운 문장으로 정리했어요.",
        ]

    @staticmethod
    def _is_friendly_evidence_text(value: str) -> bool:
        if len(value) < 8:
            return False
        if re.search(r"[가-힣]", value) is None:
            return False
        return True

    @staticmethod
    def _is_generic_evidence_text(value: str) -> bool:
        generic_evidence = {
            "공식 안내의 지원 대상 기준을 바탕으로 정리했어요.",
            "정책 상세 안내에 포함된 지원 내용을 기준으로 요약했어요.",
            "신청 방법과 제출 서류는 공식 안내 문구를 기준으로 정리했어요.",
            "공식 안내의 지원 대상 조건을 기준으로 확인했습니다.",
            "정책 상세 안내에 포함된 지원 내용을 기준으로 정리했습니다.",
            "신청 방법과 신청 전 확인사항은 공식 안내 문구를 기준으로 요약했습니다.",
            "정책 문서의 근거 자료를 바탕으로 요약했습니다.",
            "정책 상세 안내에 포함된 대상, 지원 내용, 신청 조건을 기준으로 정리했습니다.",
        }
        return value in generic_evidence

    @staticmethod
    def _is_generic_summary_text(value: str) -> bool:
        return value.strip().endswith("의 핵심 지원 내용을 간단히 정리했어요.")

    @staticmethod
    def _clean_display_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _is_internal_or_cutoff_text(value: str) -> bool:
        lowered = value.lower()
        internal_markers = (
            "...",
            "…",
            "[openapi",
            "[정책명",
            "[대분류",
            "[하위분류",
            "[급여유형",
            "condition_validation_adjusted",
            "policy_condition_profile",
            "condition_profile",
            "condition_json",
            "evidence_chunks",
            "source_text",
            "raw_",
            "matching_strength",
            "operator",
            "field",
            "quality_flags",
            "service_field",
            "rule",
            "rag",
            "chunk",
            "null",
            "undefined",
        )
        return any(marker in lowered for marker in internal_markers)

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

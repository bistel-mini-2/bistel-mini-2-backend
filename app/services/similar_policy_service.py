import asyncio
import logging
from typing import Annotated, Any

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.similar_policy_agent import SimilarPolicyAgent
from app.common.exceptions import AppException, ErrorCode
from app.repositories.policy_repository import PolicyRepository
from app.schemas.similar_policy_schema import (
    LlmSimilarPolicyItem,
    SimilarPolicyItemResponse,
    SimilarPolicyListResponse,
)
from app.services.policy_rag_service import PolicyRagService
from app.services.policy_service import PolicyService

logger = logging.getLogger(__name__)

# 벡터 후보로 쓸 정책 청크 소스(추천 후보 검색과 동일 종류).
_VECTOR_SOURCE_TYPES = ("POLICY_DETAIL", "POLICY_REFERENCE")
_PAYLOAD_TEXT_LIMIT = 200
_QUERY_TEXT_LIMIT = 1000


class SimilarPolicyService:
    """기준 정책과 유사한 정책을 찾는다.

    흐름: 기준 정책 텍스트로 벡터 유사 후보 검색 → 후보가 부족하면 규칙 기반
    관련 정책(find_related_policies)으로 보충 → 에이전트가 순위·설명 생성.
    벡터/LLM이 모두 실패해도 규칙 기반 결과로 graceful degradation 한다.
    """

    def __init__(
        self,
        rag_service: PolicyRagService | None = None,
        agent: SimilarPolicyAgent | None = None,
        vector_search_timeout_seconds: float = 20,
    ) -> None:
        self.rag_service = rag_service or PolicyRagService()
        self.agent = agent or SimilarPolicyAgent()
        self.vector_search_timeout_seconds = vector_search_timeout_seconds

    async def find_similar(
        self,
        db: AsyncSession,
        *,
        policy_slug: str,
        limit: int = 4,
    ) -> SimilarPolicyListResponse:
        target = await PolicyRepository.find_policy_detail(
            db,
            policy_slug=(policy_slug or "").strip(),
        )
        if target is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message="Policy not found",
            )
        target_id = int(target["policy_id"])

        candidate_ids = await self._vector_candidate_ids(
            target,
            exclude_id=target_id,
            want=max(limit * 3, 8),
        )
        rows = (
            await PolicyRepository.find_policies_by_ids(db, candidate_ids)
            if candidate_ids
            else []
        )
        rows_by_id = {int(row["policy_id"]): row for row in rows}
        ordered = [
            rows_by_id[pid] for pid in candidate_ids if pid in rows_by_id
        ]
        sources = {int(row["policy_id"]): "vector" for row in ordered}

        # 벡터 후보가 부족하면(임베딩 미구축 포함) 규칙 기반으로 보충.
        if len(ordered) < limit:
            ordered, sources = await self._fill_rule_based(
                db, target, target_id, ordered, sources, limit
            )
        if not ordered:
            return SimilarPolicyListResponse(items=[])

        # 에이전트에 넘기는 후보는 과도하지 않게 상한을 둔다.
        ordered = ordered[: max(limit * 2, limit)]
        judgements = await self.agent.rank(
            self._target_payload(target),
            [self._candidate_payload(row) for row in ordered],
        )
        return self._build_response(ordered, judgements, sources, limit)

    async def _vector_candidate_ids(
        self,
        target: dict[str, Any],
        exclude_id: int,
        want: int,
    ) -> list[int]:
        query = self._target_text(target)
        if not query:
            return []

        best_distance: dict[int, float] = {}
        for source_type in _VECTOR_SOURCE_TYPES:
            try:
                response = await asyncio.wait_for(
                    self.rag_service.search(
                        query=query,
                        k=max(want * 2, 20),
                        source_type=source_type,
                    ),
                    timeout=self.vector_search_timeout_seconds,
                )
            except Exception as exc:
                logger.warning(
                    "Similar vector search failed for %s: %s",
                    source_type,
                    exc,
                )
                continue

            for result in response.results:
                if result.policy_id is None:
                    continue
                policy_id = int(result.policy_id)
                if policy_id == exclude_id:
                    continue
                # 거리(distance)가 작을수록 유사 → 정책별 최소 거리를 기억.
                if (
                    policy_id not in best_distance
                    or result.distance < best_distance[policy_id]
                ):
                    best_distance[policy_id] = result.distance

        ranked = sorted(best_distance.items(), key=lambda item: item[1])
        return [policy_id for policy_id, _ in ranked[:want]]

    async def _fill_rule_based(
        self,
        db: AsyncSession,
        target: dict[str, Any],
        target_id: int,
        ordered: list[dict[str, Any]],
        sources: dict[int, str],
        limit: int,
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        have = {int(row["policy_id"]) for row in ordered}
        related = await PolicyRepository.find_related_policies(
            db,
            excluded_policy_ids=[target_id, *have],
            category=target.get("category"),
            region_scope=target.get("region_scope"),
            region_code=target.get("region_code"),
            target_stages=PolicyService._to_target_stages(target),
            tags=list(target.get("tags") or []),
            limit=(limit - len(ordered)) + 2,
        )
        for row in related:
            policy_id = int(row["policy_id"])
            if policy_id in have:
                continue
            ordered.append(row)
            have.add(policy_id)
            sources[policy_id] = "rule"
        return ordered, sources

    def _build_response(
        self,
        ordered: list[dict[str, Any]],
        judgements: list[LlmSimilarPolicyItem],
        sources: dict[int, str],
        limit: int,
    ) -> SimilarPolicyListResponse:
        rows_by_key = {str(int(row["policy_id"])): row for row in ordered}
        judged_by_key = {str(item.policy_id): item for item in judgements}

        # 에이전트가 매긴 순서를 우선하고, 판정에서 빠진 후보는 벡터/규칙 순서로 뒤에 붙인다.
        final: list[tuple[dict[str, Any], LlmSimilarPolicyItem | None]] = []
        seen: set[str] = set()
        for item in judgements:
            key = str(item.policy_id)
            if key in rows_by_key and key not in seen:
                final.append((rows_by_key[key], item))
                seen.add(key)
        for row in ordered:
            key = str(int(row["policy_id"]))
            if key not in seen:
                final.append((row, None))
                seen.add(key)

        items: list[SimilarPolicyItemResponse] = []
        for row, judgement in final[:limit]:
            policy_id = int(row["policy_id"])
            source = sources.get(policy_id, "vector")
            base = PolicyService._to_response(row)
            reason = self._clean(
                judgement.similarity_reason if judgement else None
            ) or self._rule_reason(row)
            difference = self._clean(
                judgement.difference_note if judgement else None
            )
            items.append(
                SimilarPolicyItemResponse(
                    **base.model_dump(),
                    similarity_reason=reason,
                    difference_note=difference,
                    similarity_source=source,
                )
            )
        return SimilarPolicyListResponse(items=items)

    def _target_text(self, row: dict[str, Any]) -> str:
        parts = [
            row.get("name"),
            row.get("category"),
            row.get("sub_category"),
            row.get("easy_summary") or row.get("summary"),
            row.get("benefit_description") or row.get("benefit_summary"),
            row.get("target_description"),
            " ".join(str(tag) for tag in row.get("tags") or []),
        ]
        text = " ".join(str(part) for part in parts if part)
        return " ".join(text.split())[:_QUERY_TEXT_LIMIT]

    def _target_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_id": str(int(row["policy_id"])),
            "name": row.get("name"),
            "category": row.get("category"),
            "sub_category": row.get("sub_category"),
            "summary": self._short(row.get("easy_summary") or row.get("summary")),
            "benefit": self._short(
                row.get("benefit_description") or row.get("benefit_summary")
            ),
            "target": self._short(row.get("target_description")),
            "tags": list(row.get("tags") or []),
        }

    def _candidate_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "policy_id": str(int(row["policy_id"])),
            "name": row.get("name"),
            "category": row.get("category"),
            "sub_category": row.get("sub_category"),
            "summary": self._short(row.get("summary") or row.get("benefit_summary")),
            "benefit": self._short(row.get("benefit_summary")),
            "tags": list(row.get("tags") or []),
        }

    def _rule_reason(self, row: dict[str, Any]) -> str:
        category = self._clean(row.get("category"))
        if category:
            return f"같은 '{category}' 분야의 정책이에요."
        return "비슷한 대상에게 도움이 되는 정책이에요."

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = " ".join(str(value).split())
        return text or None

    @staticmethod
    def _short(value: Any, max_len: int = _PAYLOAD_TEXT_LIMIT) -> str | None:
        if value in (None, "", []):
            return None
        text = " ".join(str(value).split())
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "…"


def get_similar_policy_service() -> SimilarPolicyService:
    # 생성자에 인자(rag_service 등 기본값)가 있어 Depends(클래스)로 직접 주입하면
    # FastAPI가 그 인자들을 요청 파라미터로 오인한다. 인자 없는 프로바이더로 감싼다.
    return SimilarPolicyService()


SimilarPolicyServiceDep = Annotated[
    SimilarPolicyService, Depends(get_similar_policy_service)
]

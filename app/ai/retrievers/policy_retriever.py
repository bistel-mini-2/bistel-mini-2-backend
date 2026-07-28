import asyncio
import json
import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Protocol

from app.common.psycopg_pool_conf import psycopg_pool
from app.repositories.policy_rag_repository import PolicyRagRepository
from app.services.policy_rag_service import PolicyRagService


class RetrievalStrategy(StrEnum):
    SQL_KEYWORD = "sql_keyword"
    VECTOR = "vector"
    HYBRID = "hybrid"
    ADAPTIVE = "adaptive"


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    chunk_id: int
    policy_id: int
    chunk_text: str
    score: float
    document_id: int | None = None
    policy_code: str | None = None
    policy_name: str | None = None
    section: str | None = None
    source_type: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    evidence_role: str | None = None
    metadata: dict[str, Any] | None = None


class PolicyRetriever(Protocol):
    strategy: RetrievalStrategy

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
        evidence_role: str | None = None,
    ) -> list[RetrievalHit]: ...


class VectorPolicyRetriever:
    strategy = RetrievalStrategy.VECTOR

    def __init__(self, rag_service: PolicyRagService | None = None) -> None:
        self.rag_service = rag_service or PolicyRagService()

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
        evidence_role: str | None = None,
    ) -> list[RetrievalHit]:
        response = await self.rag_service.search(
            query=query,
            k=top_k,
            source_type=source_type,
            policy_ids=policy_ids,
        )
        return [
            RetrievalHit(
                chunk_id=result.chunk_id,
                policy_id=result.policy_id,
                document_id=result.document_id,
                policy_code=result.policy_code,
                policy_name=result.policy_name,
                section=result.section,
                source_type=result.source_type,
                source_title=result.source_title,
                source_url=result.source_url,
                evidence_role=result.evidence_role,
                metadata=result.metadata,
                chunk_text=result.chunk_text,
                score=_distance_to_score(result.distance),
            )
            for result in response.results
            if result.chunk_id is not None and result.policy_id is not None
        ]


class SqlKeywordPolicyRetriever:
    strategy = RetrievalStrategy.SQL_KEYWORD

    def __init__(self, repository: type[PolicyRagRepository] = PolicyRagRepository):
        self.repository = repository

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
        evidence_role: str | None = None,
    ) -> list[RetrievalHit]:
        async with psycopg_pool.connection() as conn:
            rows = await self.repository.search_chunks_by_keywords(
                conn=conn,
                query=query,
                limit=top_k,
                source_type=source_type,
                policy_ids=policy_ids,
            )
        return [self._to_hit(row) for row in rows]

    @staticmethod
    def _to_hit(row: dict[str, Any]) -> RetrievalHit:
        metadata = _to_metadata(row.get("metadata_json"))
        chunk_text = str(row["chunk_text"])
        return RetrievalHit(
            chunk_id=int(row["chunk_id"]),
            policy_id=int(row["policy_id"]),
            document_id=_to_int(row.get("document_id")),
            policy_code=_to_str(row.get("policy_code")),
            policy_name=_to_str(row.get("policy_name")),
            section=(
                _to_str(metadata.get("section"))
                or _section_from_text(chunk_text)
            ),
            source_type=_to_str(row.get("source_type")),
            source_title=_to_str(row.get("source_title")),
            source_url=_to_str(row.get("source_url")),
            evidence_role=_to_str(metadata.get("evidence_role")),
            metadata=metadata,
            chunk_text=chunk_text,
            score=float(row["keyword_score"]),
        )


class HybridPolicyRetriever:
    strategy = RetrievalStrategy.HYBRID

    def __init__(
        self,
        keyword_retriever: PolicyRetriever | None = None,
        vector_retriever: PolicyRetriever | None = None,
        *,
        rrf_k: int = 60,
        candidate_multiplier: int = 2,
        keyword_weight: float = 0.25,
        vector_weight: float = 1.0,
        max_chunks_per_policy: int | None = 2,
    ) -> None:
        self.keyword_retriever = keyword_retriever or SqlKeywordPolicyRetriever()
        self.vector_retriever = vector_retriever or VectorPolicyRetriever()
        self.rrf_k = rrf_k
        self.candidate_multiplier = candidate_multiplier
        self.keyword_weight = keyword_weight
        self.vector_weight = vector_weight
        self.max_chunks_per_policy = max_chunks_per_policy

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
        evidence_role: str | None = None,
    ) -> list[RetrievalHit]:
        candidate_k = max(top_k * self.candidate_multiplier, top_k)
        keyword_hits, vector_hits = await asyncio.gather(
            self.keyword_retriever.retrieve(
                query,
                top_k=candidate_k,
                source_type=source_type,
                policy_ids=policy_ids,
                evidence_role=evidence_role,
            ),
            self.vector_retriever.retrieve(
                query,
                top_k=candidate_k,
                source_type=source_type,
                policy_ids=policy_ids,
                evidence_role=evidence_role,
            ),
        )
        return self.fuse_hits(
            keyword_hits,
            vector_hits,
            top_k=top_k,
            policy_ids=policy_ids,
        )

    def fuse_hits(
        self,
        keyword_hits: list[RetrievalHit],
        vector_hits: list[RetrievalHit],
        *,
        top_k: int,
        policy_ids: list[int | str] | None,
    ) -> list[RetrievalHit]:
        fused_scores: dict[int, float] = {}
        hits_by_chunk_id: dict[int, RetrievalHit] = {}
        for hits, weight in (
            (keyword_hits, self.keyword_weight),
            (vector_hits, self.vector_weight),
        ):
            for rank, hit in enumerate(hits, start=1):
                hits_by_chunk_id.setdefault(hit.chunk_id, hit)
                fused_scores[hit.chunk_id] = (
                    fused_scores.get(hit.chunk_id, 0.0)
                    + weight / (self.rrf_k + rank)
                )

        ranked_chunk_ids = sorted(
            fused_scores,
            key=lambda chunk_id: (-fused_scores[chunk_id], chunk_id),
        )
        ranked_hits = [
            replace(
                hits_by_chunk_id[chunk_id],
                score=fused_scores[chunk_id],
            )
            for chunk_id in ranked_chunk_ids
        ]
        if policy_ids or self.max_chunks_per_policy is None:
            return ranked_hits[:top_k]

        selected: list[RetrievalHit] = []
        policy_counts: dict[int, int] = {}
        policy_cap = min(
            self.max_chunks_per_policy,
            max(1, top_k // 2),
        )
        for hit in ranked_hits:
            count = policy_counts.get(hit.policy_id, 0)
            if count >= policy_cap:
                continue
            selected.append(hit)
            policy_counts[hit.policy_id] = count + 1
            if len(selected) == top_k:
                break
        return selected


class AdaptivePolicyRetriever:
    strategy = RetrievalStrategy.ADAPTIVE
    _SECTION_ROLES = frozenset(
        {"SUMMARY", "TARGET", "BENEFIT", "APPLICATION", "CAUTION"}
    )

    def __init__(
        self,
        vector_retriever: PolicyRetriever | None = None,
        keyword_retriever: PolicyRetriever | None = None,
        *,
        candidate_multiplier: int = 2,
    ) -> None:
        self.vector_retriever = vector_retriever or VectorPolicyRetriever()
        self.keyword_retriever = keyword_retriever or SqlKeywordPolicyRetriever()
        self.candidate_multiplier = candidate_multiplier
        self.fusion = HybridPolicyRetriever(
            keyword_retriever=self.keyword_retriever,
            vector_retriever=self.vector_retriever,
            candidate_multiplier=candidate_multiplier,
        )
        self.fallback_count = 0

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        source_type: str | None = None,
        policy_ids: list[int | str] | None = None,
        evidence_role: str | None = None,
    ) -> list[RetrievalHit]:
        candidate_k = max(top_k * self.candidate_multiplier, top_k)
        vector_hits = await self.vector_retriever.retrieve(
            query,
            top_k=top_k,
            source_type=source_type,
            policy_ids=policy_ids,
            evidence_role=evidence_role,
        )
        visible_vector_hits = vector_hits[:top_k]
        fallback_reason = self._fallback_reason(
            visible_vector_hits,
            evidence_role=evidence_role,
        )
        if fallback_reason is None:
            return visible_vector_hits

        self.fallback_count += 1
        keyword_hits = await self.keyword_retriever.retrieve(
            query,
            top_k=candidate_k,
            source_type=source_type,
            policy_ids=policy_ids,
            evidence_role=evidence_role,
        )
        if fallback_reason == "missing_role":
            return self._supplement_required_role(
                visible_vector_hits,
                keyword_hits,
                top_k=top_k,
                evidence_role=evidence_role,
            )
        return self.fusion.fuse_hits(
            keyword_hits,
            vector_hits,
            top_k=top_k,
            policy_ids=policy_ids,
        )

    @classmethod
    def _fallback_reason(
        cls,
        hits: list[RetrievalHit],
        *,
        evidence_role: str | None,
    ) -> str | None:
        if not hits:
            return "empty"

        required_role = str(evidence_role or "").strip().upper()
        if required_role in cls._SECTION_ROLES and not any(
            required_role in _hit_roles(hit) for hit in hits
        ):
            return "missing_role"

        return None

    @staticmethod
    def _supplement_required_role(
        vector_hits: list[RetrievalHit],
        keyword_hits: list[RetrievalHit],
        *,
        top_k: int,
        evidence_role: str | None,
    ) -> list[RetrievalHit]:
        required_role = str(evidence_role or "").strip().upper()
        vector_chunk_ids = {hit.chunk_id for hit in vector_hits}
        supplements = [
            hit
            for hit in keyword_hits
            if hit.chunk_id not in vector_chunk_ids
            and required_role in _hit_roles(hit)
        ]
        if not supplements:
            return vector_hits[:top_k]

        supplement = supplements[0]
        kept_vector_hits = vector_hits[: max(top_k - 1, 0)]
        return [*kept_vector_hits, supplement][:top_k]


def build_policy_retriever(
    strategy: RetrievalStrategy | str = RetrievalStrategy.VECTOR,
    *,
    rag_service: PolicyRagService | None = None,
) -> PolicyRetriever:
    selected = RetrievalStrategy(strategy)
    if selected is RetrievalStrategy.SQL_KEYWORD:
        return SqlKeywordPolicyRetriever()
    if selected is RetrievalStrategy.HYBRID:
        return HybridPolicyRetriever(
            vector_retriever=VectorPolicyRetriever(rag_service=rag_service)
        )
    if selected is RetrievalStrategy.ADAPTIVE:
        return AdaptivePolicyRetriever(
            vector_retriever=VectorPolicyRetriever(rag_service=rag_service)
        )
    return VectorPolicyRetriever(rag_service=rag_service)


def _distance_to_score(distance: float | None) -> float:
    if distance is None:
        return 0.0
    return 1 / (1 + max(float(distance), 0.0))


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _to_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _section_from_text(value: str) -> str | None:
    match = re.search(r"^섹션:\s*(.+?)\s*$", value, re.MULTILINE)
    return match.group(1).strip() if match else None


def _hit_roles(hit: RetrievalHit) -> set[str]:
    roles: set[str] = set()
    if hit.evidence_role:
        roles.add(str(hit.evidence_role).strip().upper())
    role_by_section = {
        "기본 정보": "SUMMARY",
        "요약": "SUMMARY",
        "지원 대상": "TARGET",
        "정리된 지원 조건": "TARGET",
        "공식 지원대상 원문": "TARGET",
        "지원 내용": "BENEFIT",
        "신청 방법": "APPLICATION",
        "신청 기간": "APPLICATION",
        "유의 사항": "CAUTION",
    }
    section_role = role_by_section.get(str(hit.section or "").strip())
    if section_role:
        roles.add(section_role)
    return roles

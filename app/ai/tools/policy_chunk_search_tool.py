import re

from app.ai.retrievers import (
    PolicyRetriever,
    RetrievalStrategy,
    build_policy_retriever,
)
from app.schemas.ai_contract import EvidenceChunk
from app.services.policy_rag_service import PolicyRagService

_RAW_STRUCTURED_RE = re.compile(
    r"\b(?:operator|matchingstrength|confidence):\s*\S"
    r"|\bsourcetext:\s*\S.*\breason:\s*[A-Z_]{5}",
    re.IGNORECASE | re.DOTALL,
)

ROLE_BY_SECTION = {
    "기본 정보": "SUMMARY",
    "요약": "SUMMARY",
    "지원 대상": "TARGET",
    "지원 내용": "BENEFIT",
    "신청 방법": "APPLICATION",
    "신청 기간": "APPLICATION",
    "유의 사항": "CAUTION",
}


async def search_policy_chunks(
    query: str,
    policy_ids: list[int | str] | None = None,
    top_k: int = 5,
    evidence_role: str | None = None,
    rag_service: PolicyRagService | None = None,
    strategy: RetrievalStrategy | str = RetrievalStrategy.VECTOR,
    retriever: PolicyRetriever | None = None,
) -> list[EvidenceChunk]:
    selected_retriever = retriever or build_policy_retriever(
        strategy,
        rag_service=rag_service,
    )
    results = await selected_retriever.retrieve(
        query,
        top_k=top_k,
        policy_ids=policy_ids,
    )

    allowed_policy_ids = {str(policy_id) for policy_id in policy_ids or []}
    chunks: list[EvidenceChunk] = []
    for result in results:
        if _RAW_STRUCTURED_RE.search(result.chunk_text or ""):
            continue

        policy_keys = {str(result.policy_id)}
        if result.policy_code:
            policy_keys.add(result.policy_code)
        if allowed_policy_ids and not (policy_keys & allowed_policy_ids):
            continue

        chunks.append(
            EvidenceChunk(
                chunk_id=result.chunk_id,
                policy_id=result.policy_id,
                snippet=result.chunk_text,
                source_title=(
                    result.source_title
                    or _source_title(result.policy_name, result.section)
                ),
                source_url=result.source_url or "",
                score=result.score,
                evidence_role=(
                    result.evidence_role
                    or _evidence_role(result.section)
                    or evidence_role
                ),
            )
        )
    return chunks


def _source_title(policy_name: str | None, section: str | None) -> str:
    if policy_name and section:
        return f"{policy_name} - {section}"
    return policy_name or section or "정책 근거"


def _evidence_role(section: str | None) -> str | None:
    if section is None:
        return None
    return ROLE_BY_SECTION.get(section)

from app.schemas.ai_contract import EvidenceChunk
from app.services.policy_rag_service import PolicyRagService


async def search_policy_chunks(
    query: str,
    policy_ids: list[int | str] | None = None,
    top_k: int = 5,
    evidence_role: str | None = None,
    rag_service: PolicyRagService | None = None,
) -> list[EvidenceChunk]:
    service = rag_service or PolicyRagService()
    response = await service.search(query=query, k=top_k, policy_ids=policy_ids)

    allowed_policy_ids = {str(policy_id) for policy_id in policy_ids or []}
    chunks: list[EvidenceChunk] = []
    for result in response.results:
        if result.chunk_id is None or result.policy_id is None:
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
                source_title=_source_title(result.policy_name, result.section),
                source_url=result.source_url or "",
                score=_distance_to_score(result.distance),
                evidence_role=evidence_role,
            )
        )
    return chunks


def _source_title(policy_name: str | None, section: str | None) -> str:
    if policy_name and section:
        return f"{policy_name} - {section}"
    return policy_name or section or "정책 근거"


def _distance_to_score(distance: float | None) -> float | None:
    if distance is None:
        return None
    return 1 / (1 + max(float(distance), 0.0))

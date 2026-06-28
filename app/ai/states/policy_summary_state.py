from typing import Any, NotRequired, TypedDict

from app.schemas.ai_contract import EvidenceChunk


class PolicySummaryGraphState(TypedDict):
    policy: dict[str, Any]
    evidence_chunks: NotRequired[list[EvidenceChunk]]
    summary: NotRequired[str]
    evidence: NotRequired[list[str]]


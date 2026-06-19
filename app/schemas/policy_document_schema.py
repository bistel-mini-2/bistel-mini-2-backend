from pydantic import BaseModel


class PolicyDocumentChunkIngestItem(BaseModel):
    policy_id: int
    document_id: int
    policy_code: str
    policy_name: str
    raw_text_length: int
    chunk_count: int


class PolicyDocumentChunkSkipItem(BaseModel):
    policy_id: int
    policy_code: str
    policy_name: str
    reason: str


class PolicyDocumentChunkIngestResponse(BaseModel):
    requested_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    items: list[PolicyDocumentChunkIngestItem]
    skipped: list[PolicyDocumentChunkSkipItem]
    failed: list[dict[str, str]]

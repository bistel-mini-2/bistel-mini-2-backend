from pydantic import BaseModel


class PolicyDocumentChunkIngestItem(BaseModel):
    policy_id: int
    condition_profile_id: int | None = None
    document_id: int
    policy_code: str
    policy_name: str
    raw_text_length: int
    chunk_count: int
    deleted_embedding_count: int = 0


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


class PolicyReferenceDocumentIngestItem(BaseModel):
    document_id: int
    policy_id: int
    policy_code: str
    policy_name: str
    source_title: str
    source_url: str
    file_type: str
    raw_text_length: int
    chunk_count: int


class PolicyReferenceDocumentSkipItem(BaseModel):
    document_id: int
    policy_id: int
    policy_code: str
    policy_name: str
    source_title: str
    source_url: str | None = None
    reason: str


class PolicyReferenceDocumentIngestResponse(BaseModel):
    requested_url_count: int
    completed_count: int
    skipped_count: int
    failed_count: int
    items: list[PolicyReferenceDocumentIngestItem]
    skipped: list[PolicyReferenceDocumentSkipItem]
    failed: list[dict[str, str]]

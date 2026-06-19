from pydantic import BaseModel


class PolicyRagEmbeddingItem(BaseModel):
    chunk_id: int
    document_id: int
    policy_id: int
    policy_code: str
    policy_name: str


class PolicyRagEmbeddingResponse(BaseModel):
    requested_count: int
    embedded_count: int
    collection_name: str
    items: list[PolicyRagEmbeddingItem]


class PolicyRagSearchResult(BaseModel):
    chunk_id: int | None = None
    document_id: int | None = None
    policy_id: int | None = None
    policy_code: str | None = None
    policy_name: str | None = None
    section: str | None = None
    source_type: str | None = None
    source_url: str | None = None
    chunk_text: str
    distance: float


class PolicyRagSearchResponse(BaseModel):
    query: str
    result_count: int
    results: list[PolicyRagSearchResult]

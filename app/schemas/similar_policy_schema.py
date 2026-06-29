from pydantic import BaseModel, Field

from app.schemas.policy_schema import PolicyListItemResponse


# --- 에이전트 입출력 ---
class LlmSimilarPolicyItem(BaseModel):
    """에이전트가 판단한 후보 1건(유사 순서대로)."""

    policy_id: str
    similarity_reason: str = ""  # 왜 비슷한지 사용자 친화 1문장
    difference_note: str = ""  # 기준 정책과의 핵심 차이 1문장(없으면 빈 값)


class LlmSimilarPolicyResult(BaseModel):
    items: list[LlmSimilarPolicyItem] = Field(default_factory=list)


# --- API 응답 ---
class SimilarPolicyItemResponse(PolicyListItemResponse):
    """정책 목록 항목 + 유사 설명. 정책상세/추천/비교/채팅에서 공용으로 쓴다."""

    similarity_reason: str | None = None
    difference_note: str | None = None
    # vector | rule — 후보가 어떤 경로로 뽑혔는지(표시·디버그용).
    similarity_source: str = "vector"


class SimilarPolicyListResponse(BaseModel):
    items: list[SimilarPolicyItemResponse] = Field(default_factory=list)

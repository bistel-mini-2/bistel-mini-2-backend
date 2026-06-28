from pydantic import BaseModel, Field

from app.common.ai_status import AssessmentStatus


class LlmCandidateJudgement(BaseModel):
    """후보 정책 1건에 대한 LLM 적합도 판정 결과."""

    policy_id: str = Field(description="입력으로 받은 후보의 policy_id")
    assessment_status: AssessmentStatus = Field(
        description="사용자 조건과 정책의 부합 정도 판정"
    )
    reason_summary: str = Field(
        default="",
        description=(
            "판정 이유 한 줄 요약. 사용자 친화 한국어로, "
            "시스템/내부 용어나 규칙 코드는 쓰지 않는다."
        ),
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description=(
            "확정 판정을 위해 사용자에게 추가로 확인해야 할 정보(부족 조건). "
            "예: '의료급여 수급 여부', '자녀의 정확한 개월 수'. "
            "부족한 정보가 없으면 빈 배열."
        ),
    )


class LlmRecommendationJudgementResult(BaseModel):
    """후보 풀 전체에 대한 배치 판정 결과."""

    judgements: list[LlmCandidateJudgement] = Field(default_factory=list)

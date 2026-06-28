import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.schemas.recommendation_judgement_schema import (
    LlmCandidateJudgement,
    LlmRecommendationJudgementResult,
)

logger = logging.getLogger(__name__)


class RecommendationJudgementAgent:
    """추천 후보의 적합도(verdict)와 부족 정보를 LLM이 판정하는 에이전트.

    검색·룰 하드필터를 통과한 후보 풀을 배치 1회 호출로 판정한다.
    실패하면 빈 목록을 반환해, 호출부가 룰 기반 판정으로 fallback하도록 한다.
    후보 payload는 호출부가 만들어 넘기며, 에이전트는 LLM 입출력만 책임진다.
    """

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 40,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def judge(
        self,
        user_condition: dict[str, Any],
        candidate_payloads: list[dict[str, Any]],
    ) -> list[LlmCandidateJudgement]:
        if not candidate_payloads:
            return []

        allowed_ids = {
            str(payload.get("policy_id")) for payload in candidate_payloads
        }
        try:
            from langchain_openai import ChatOpenAI

            llm_kwargs: dict[str, Any] = {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 1500,
                "timeout": self.timeout_seconds,
            }
            if settings.openai_api_key:
                llm_kwargs["api_key"] = settings.openai_api_key

            structured_llm = ChatOpenAI(**llm_kwargs).with_structured_output(
                LlmRecommendationJudgementResult
            )
            messages = [
                ("system", self._system_prompt()),
                (
                    "user",
                    json.dumps(
                        {
                            "user_condition": user_condition,
                            "candidate_policies": candidate_payloads,
                        },
                        ensure_ascii=False,
                    ),
                ),
            ]
            result = await asyncio.wait_for(
                structured_llm.ainvoke(messages),
                timeout=self.timeout_seconds,
            )
            if isinstance(result, dict):
                result = LlmRecommendationJudgementResult.model_validate(result)
            if not isinstance(result, LlmRecommendationJudgementResult):
                return []
        except Exception as exc:
            logger.warning(
                "Recommendation judgement LLM failed, using rule fallback: %s: %s",
                type(exc).__name__,
                exc,
            )
            return []

        # 입력으로 받은 policy_id만 신뢰한다(환각 policy_id 차단).
        return [
            judgement
            for judgement in result.judgements
            if str(judgement.policy_id) in allowed_ids
        ]

    def _system_prompt(self) -> str:
        return """
당신은 한국 복지정책 추천 서비스의 적합도 판정기입니다.
이미 검색·룰 하드필터를 통과한 후보 정책들에 대해, 사용자 조건과 정책 조건이
얼마나 부합하는지 후보별로 판정합니다.

각 후보에 대해 assessment_status를 아래에서 하나 고릅니다.
- LIKELY_MATCH: 사용자 조건과 정책 대상/혜택이 대체로 부합합니다.
- NEEDS_MORE_INFO: 부합 가능성은 있으나 일부 조건 확인이 더 필요합니다.
- NOT_MATCH: 정책 조건과 명확히 어긋납니다.
- INSUFFICIENT_PROFILE: 사용자 조건이 거의 없어 판단이 어렵습니다.
- CONFLICTING_PROFILE: 사용자 조건끼리 또는 정책과 충돌합니다.

규칙:
1. 제공된 정책 정보(target/benefit/조건)와 사용자 조건 안에서만 판단하고, 없는 내용을 추측하지 않습니다.
2. 이 후보들은 이미 하드 불일치 필터를 통과했으므로, 명백히 어긋날 때만 NOT_MATCH로 둡니다. 애매하면 NEEDS_MORE_INFO를 우선합니다.
3. 지원 자격을 "확정"으로 단정하지 않습니다(추천 보조 판단).
4. reason_summary는 사용자 친화 한국어 1문장으로, 어떤 사용자 조건과 어떤 정책 조건이 맞거나 확인이 필요한지 설명합니다. 시스템/내부 용어, 대문자 규칙 코드는 쓰지 않습니다.
5. missing_information에는 확정 판정을 위해 사용자에게 더 물어봐야 할 정보만 짧게 담습니다(없으면 빈 배열). 예: "의료급여 수급 여부", "자녀의 정확한 개월 수".
6. 입력으로 받은 policy_id만 사용하고, 모든 후보에 대해 판정을 출력합니다.
""".strip()

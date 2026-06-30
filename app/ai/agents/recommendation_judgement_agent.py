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
        model: str = "gpt-5.4-mini",
        timeout_seconds: float = 90,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def judge(
        self,
        user_condition: dict[str, Any],
        candidate_payloads: list[dict[str, Any]],
        follow_up_answers: list[dict[str, Any]] | None = None,
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
                            "follow_up_answers": follow_up_answers or [],
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
4. 정책 자격이 사용자가 아직 밝히지 않은 "결정적 조건"에 달려 있으면 LIKELY_MATCH로 단정하지 말고 NEEDS_MORE_INFO로 두고, 그 확인 항목을 missing_information에 구체적으로 담습니다. 결정적 조건의 예:
   - 수급 자격(기초생활보장/생계·의료·주거·교육 급여/차상위계층/한부모가족 수급 등). 사용자가 소득 "구간"만 말하고 수급 자격을 밝히지 않았으면, 수급 자격 정책은 "소득이 낮으니 잘 맞음"으로 단정하지 말고 수급 자격 여부를 묻습니다.
   - 출생신고/주민등록 여부, 장애 정도, 위기아동·가정보호 해당 여부 등 자격을 가르는 핵심 상태.
   이런 항목은 카드 "확인사항"으로만 미루지 말고 missing_information(=추가질문)에 올립니다.
4-1. follow_up_answers는 위 "결정적 조건"에 대해 사용자가 추가질문에 직접 답한 결과다. 이 답변을 반드시 최우선으로 반영한다(질문 텍스트가 아니라 "사용자 답변"이 사실이다).
   - 사용자가 어떤 조건을 "없음/아니오/해당 없음/필요 없음/안 해당" 취지로 답했는데, 그 조건을 핵심 요구·대상으로 하는 정책이면 → 반드시 NOT_MATCH로 판정한다(더 이상 NEEDS_MORE_INFO로 미루지 않는다).
     예: "기초생활·차상위 등 수급 자격이 있으신가요? → 없어요" 이면, 수급 자격을 요구하는 저소득·수급 전용 정책(예: 저소득층 기저귀·조제분유 지원)은 NOT_MATCH.
     예: "법률 상담이 필요하세요? → 필요 없어요" 이면, 법률 상담 제공이 목적인 정책(예: 무료법률상담, 개인회생·파산 법률지원)은 NOT_MATCH.
   - 반대로 "있음/예/해당함" 취지로 답했으면 그 조건은 충족된 것으로 보고 그 정책의 NEEDS_MORE_INFO를 풀어 LIKELY_MATCH 쪽으로 본다.
   - 답변이 모호하거나 그 정책과 무관한 답변이면 기존 판정을 유지한다(억지로 NOT_MATCH로 만들지 않는다).
   - 중요: "소득 구간이 낮음"(예: 중위소득 50% 이하)과 "수급 자격(기초생활/차상위/한부모가족 수급)"은 별개다. 사용자가 수급 자격이 "없다"고 답했으면, 소득이 낮더라도 수급 자격을 요구하는 정책은 NOT_MATCH로 둔다. "소득이 낮으니 저소득층/수급자에 해당한다"고 추론하지 않는다.
   - 정책 자격이 여러 수급 유형의 OR(예: 기초생활 OR 차상위 OR 한부모가족 수급)인 경우, 사용자가 그 수급 자격이 없다고 했고(income_status="none") 해당 특수상황(한부모 등 special)에도 해당하지 않으면 NOT_MATCH로 둔다.
5. reason_summary는 사용자 친화 한국어 1문장으로, 어떤 사용자 조건과 어떤 정책 조건이 맞거나 확인이 필요한지 설명합니다. 시스템/내부 용어, 대문자 규칙 코드는 쓰지 않습니다.
6. missing_information에는 확정 판정을 위해 사용자에게 더 물어봐야 할 정보만 짧게 담습니다. 예: "기초생활/차상위 등 수급 자격 여부", "자녀의 정확한 개월 수". NEEDS_MORE_INFO나 INSUFFICIENT_PROFILE로 판정하면 반드시 1개 이상 채웁니다(더 물어볼 게 없는 LIKELY_MATCH만 빈 배열).
7. 입력으로 받은 policy_id만 사용하고, 모든 후보에 대해 판정을 출력합니다.
""".strip()

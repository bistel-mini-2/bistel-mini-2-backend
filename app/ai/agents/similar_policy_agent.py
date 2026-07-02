import asyncio
import json
import logging
from typing import Any

from app.core.config import settings
from app.schemas.similar_policy_schema import (
    LlmSimilarPolicyItem,
    LlmSimilarPolicyResult,
)

logger = logging.getLogger(__name__)


class SimilarPolicyAgent:
    """기준 정책과 벡터로 뽑힌 후보들을 받아, 유사한 순서로 정렬하고
    각 후보가 왜 비슷한지를 설명하는 에이전트.

    후보 검색(벡터)은 호출부가 끝낸 뒤 후보 payload만 넘기고, 에이전트는
    순위·설명 생성만 책임진다. 실패하면 빈 목록을 반환해 호출부가 벡터 순서 +
    규칙 사유로 fallback하도록 한다.
    """

    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        timeout_seconds: float = 60,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def rank(
        self,
        target_policy: dict[str, Any],
        candidate_payloads: list[dict[str, Any]],
    ) -> list[LlmSimilarPolicyItem]:
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
                "max_tokens": 1200,
                "timeout": self.timeout_seconds,
            }
            if settings.openai_api_key:
                llm_kwargs["api_key"] = settings.openai_api_key

            structured_llm = ChatOpenAI(**llm_kwargs).with_structured_output(
                LlmSimilarPolicyResult
            )
            messages = [
                ("system", self._system_prompt()),
                (
                    "user",
                    json.dumps(
                        {
                            "target_policy": target_policy,
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
                result = LlmSimilarPolicyResult.model_validate(result)
            if not isinstance(result, LlmSimilarPolicyResult):
                return []
        except Exception as exc:
            logger.warning(
                "Similar policy LLM failed, using vector/rule fallback: %s: %s",
                type(exc).__name__,
                exc,
            )
            return []

        # 입력으로 받은 policy_id만 신뢰한다(환각 policy_id 차단).
        return [
            item
            for item in result.items
            if str(item.policy_id) in allowed_ids
        ]

    def _system_prompt(self) -> str:
        return """
당신은 한국 복지정책 비교 도우미입니다.
기준 정책(target_policy)과 후보 정책들(candidate_policies)을 받습니다.
기준 정책과 의미적으로 비슷한 순서로 후보를 정렬하고, 각 후보가 왜 비슷한지 설명합니다.

규칙:
1. 제공된 정책 정보(대상/혜택/분류/태그) 안에서만 판단하고, 없는 내용을 지어내지 않습니다.
2. items에는 기준 정책과 유사한 후보부터 순서대로 담습니다. 분명히 무관한 후보는 빼도 됩니다.
3. 유사성은 "겉으로 분야가 같은지"보다 사용자 입장에서의 목적·대상·혜택이 비슷한지를 우선합니다.
4. similarity_reason: 기준 정책과 어떤 점(대상·혜택·목적·생애주기)이 비슷한지 사용자 친화 한국어 1문장.
5. difference_note: 기준 정책과의 핵심 차이(혜택 형태, 대상 범위, 지원 시기 등)를 1문장으로. 차이가 뚜렷하지 않으면 빈 문자열로 둡니다.
6. 시스템/내부 용어나 대문자 규칙 코드(예: NATIONAL, LIKELY_MATCH)는 쓰지 않습니다.
7. 입력으로 받은 policy_id만 사용합니다.
""".strip()

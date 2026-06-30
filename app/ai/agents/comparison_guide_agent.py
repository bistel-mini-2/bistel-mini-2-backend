import asyncio
import json
import logging
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.compare_schema import CompareDiffItem


logger = logging.getLogger(__name__)


class ComparisonGuideResult(BaseModel):
    selection_guide: str = Field(min_length=20, max_length=900)


class ComparisonGuideAgent:
    """정책 비교 결과를 사용자 상황별 선택 가이드 문장으로 정리한다."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 10,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def rewrite_selection_guide(
        self,
        *,
        policy_a: dict[str, Any],
        policy_b: dict[str, Any],
        diff_table: list[CompareDiffItem],
        fallback_guide: str,
    ) -> str:
        if not settings.openai_api_key:
            logger.info("정책 비교 선택 가이드 LLM 건너뜀: OPENAI_API_KEY 없음")
            return fallback_guide

        try:
            from langchain_openai import ChatOpenAI

            logger.info(
                "정책 비교 선택 가이드 LLM 생성 시작: policy_a=%s policy_b=%s",
                policy_a.get("name"),
                policy_b.get("name"),
            )
            structured_llm = ChatOpenAI(
                model=self.model,
                temperature=0.2,
                max_completion_tokens=900,
                timeout=self.timeout_seconds,
                max_retries=0,
                api_key=settings.openai_api_key,
            ).with_structured_output(ComparisonGuideResult)

            result = await asyncio.wait_for(
                structured_llm.ainvoke(
                    [
                        ("system", self._system_prompt()),
                        (
                            "user",
                            json.dumps(
                                self._json_safe(
                                    {
                                        "policy_a": self._policy_payload(policy_a),
                                        "policy_b": self._policy_payload(policy_b),
                                        "diff_table": [
                                            item.model_dump() for item in diff_table
                                        ],
                                        "fallback_guide": fallback_guide,
                                    }
                                ),
                                ensure_ascii=False,
                            ),
                        ),
                    ]
                ),
                timeout=self.timeout_seconds,
            )
            if isinstance(result, dict):
                result = ComparisonGuideResult.model_validate(result)
            if not isinstance(result, ComparisonGuideResult):
                return fallback_guide
        except Exception as exc:
            logger.warning(
                "정책 비교 선택 가이드 LLM 생성 실패, fallback 사용: %s: %s",
                type(exc).__name__,
                exc,
            )
            return fallback_guide

        guide = " ".join(result.selection_guide.split())
        logger.info(
            "정책 비교 선택 가이드 LLM 생성 완료: policy_a=%s policy_b=%s",
            policy_a.get("name"),
            policy_b.get("name"),
        )
        return guide or fallback_guide

    def _policy_payload(self, policy: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": policy.get("name"),
            "category": policy.get("category"),
            "benefit_type": policy.get("benefit_type"),
            "target_summary": policy.get("condition_profile_target_summary"),
            "condition_source_text": policy.get("condition_profile_source_text"),
            "review_required": policy.get("condition_profile_review_required"),
            "confidence": policy.get("condition_profile_confidence"),
            "required_documents": policy.get("required_documents") or [],
        }

    def _json_safe(self, value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, BaseModel):
            return self._json_safe(value.model_dump())
        if isinstance(value, dict):
            return {
                str(key): self._json_safe(item)
                for key, item in value.items()
            }
        if isinstance(value, list | tuple | set):
            return [self._json_safe(item) for item in value]
        return value

    def _system_prompt(self) -> str:
        return """
당신은 복지 정책 비교 결과를 사용자에게 설명하는 상담형 작성자입니다.
두 정책의 조건, 혜택 유형, 제출 서류, 주의 조건, 비교표를 받아 각 정책의 장점과 활용하기 좋은 상황을 사용자가 바로 이해할 수 있게 정리합니다.
지원 가능성 자체는 별도 기능에서 판단하므로, 이 가이드는 "자격을 다시 확인하라"가 아니라 "두 정책이 각각 어떤 점에서 좋은지"를 알려주는 데 집중합니다.

작성 규칙:
1. 반드시 한국어로 작성합니다.
2. field, operator, value, condition_json, matching_strength 같은 내부 필드명이나 JSON 구조는 노출하지 않습니다.
3. 없는 사실을 만들지 않습니다. 입력에 없는 금액, 기간, 기관, 확정 가능 여부를 추측하지 않습니다.
4. "조건을 먼저 비교하세요", "본인 상황에 더 가까운 정책을 선택하세요"처럼 사용자가 이미 화면에서 할 수 있는 당연한 말은 피합니다.
5. 한쪽을 무조건 추천하지 말고, A 정책의 좋은 점과 B 정책의 좋은 점을 각각 짚어줍니다.
6. 가능하면 "A는 비용 부담을 줄이는 데", "B는 돌봄 공백을 메우는 데"처럼 혜택의 성격이나 사용자가 얻는 이점을 중심으로 설명합니다.
7. 사용자가 읽기 쉽게 3~5문장으로 작성합니다. 목록 기호 없이 자연스러운 문단으로 씁니다.
8. 조건 정보가 부족하거나 review_required가 있으면 마지막 문장에서만 부드럽게 공식 안내 확인 필요성을 덧붙입니다.
9. 제출 서류는 판단 근거 문서가 아니라 실제 신청 준비 부담 관점에서만 언급합니다.
10. 정책 이름은 정확히 사용하되 너무 반복하지 않습니다.
""".strip()

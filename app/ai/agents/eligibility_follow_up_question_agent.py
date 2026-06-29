import asyncio
import json
import logging
import os
import re
from typing import Any

from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


class FollowUpQuestionRewriteItem(BaseModel):
    index: int
    question_text: str = Field(min_length=1, max_length=200)


class FollowUpQuestionRewriteResult(BaseModel):
    questions: list[FollowUpQuestionRewriteItem] = Field(default_factory=list)


class EligibilityFollowUpQuestionAgent:
    """추가 확인 조건을 사용자가 답할 수 있는 질문 문장으로 변환한다."""

    _INTERNAL_QUESTION_BY_CODE = {
        "OVERSEAS_STAY_90_DAYS_PAYMENT_SUSPENDED": (
            "최근 90일 이상 해외에 체류하여 급여 지급이 정지된 상태인가요?"
        ),
        "REFUGEE_APPLICATION_PENDING_EXCLUDED": (
            "현재 난민 인정 심사 중인 상태인가요?"
        ),
        "DETAILED_DISABILITY_TYPE_NOT_COLLECTED": (
            "정책에서 요구하는 세부 장애 유형에 해당하시나요?"
        ),
        "CALCULATION_RULE_NOT_TARGET_EXCLUSION": (
            "정책의 계산식 또는 예외 기준에 해당하는지 공식 안내로 확인하셨나요?"
        ),
        "EXPLICIT_EXCLUSION": "정책의 제외 대상 조건에 해당하시나요?",
    }

    def to_question(self, value: Any) -> str | None:
        text = self._clean(value)
        if not text:
            return None

        mapped = self._INTERNAL_QUESTION_BY_CODE.get(text)
        if mapped:
            return mapped
        if self._is_internal_code(text):
            return None

        text = self._strip_verdict_suffix(text)
        if not text or self._is_internal_code(text):
            return None
        if text.endswith("?"):
            return text
        if self._looks_like_instruction(text):
            return text
        return f"{text}에 해당하시나요?"

    async def rewrite_questions(
        self,
        points: list[str],
        policy_name: str | None = None,
        timeout_seconds: float = 20,
    ) -> dict[str, str]:
        """manual_check_point 원본 → 사용자용 추가 질문 문장 매핑을 만든다.

        템플릿(to_question)으로 baseline 질문을 만들고, LLM이 있으면 더 자연스러운
        한국어 질문으로 다듬는다. 키가 답할 수 없는 내부 코드면 매핑에서 제외한다.
        evidence 문장화(rewrite_display_texts)와 동일하게, LLM 키 없음/실패 시
        baseline 템플릿으로 fallback해 추가 질문이 사라지지 않게 한다.

        반환 dict의 키는 원본 point(매칭 키)이므로, LLM이 문장을 바꿔도
        답변 병합은 키 기준으로 안전하게 동작한다.
        """
        baseline: dict[str, str] = {}
        for point in points:
            text = self.to_question(point)
            if text:
                baseline[point] = text
        if not baseline or not os.getenv("OPENAI_API_KEY"):
            return baseline

        ordered_points = list(baseline.keys())
        try:
            from langchain_openai import ChatOpenAI

            structured_llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0,
                max_completion_tokens=600,
                timeout=timeout_seconds,
                max_retries=0,
            ).with_structured_output(FollowUpQuestionRewriteResult)
            result = await asyncio.wait_for(
                structured_llm.ainvoke(
                    [
                        ("system", self._rewrite_system_prompt()),
                        (
                            "user",
                            json.dumps(
                                {
                                    "policy_name": policy_name,
                                    "questions": [
                                        {
                                            "index": index,
                                            "missing_point": point,
                                            "baseline_question": baseline[point],
                                        }
                                        for index, point in enumerate(ordered_points)
                                    ],
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    ]
                ),
                timeout=timeout_seconds,
            )
            if isinstance(result, dict):
                result = FollowUpQuestionRewriteResult.model_validate(result)
            if not isinstance(result, FollowUpQuestionRewriteResult):
                return baseline
        except Exception as exc:
            logger.warning(
                "추가 질문 LLM 문장화 실패, 템플릿 fallback 사용: %s: %s",
                type(exc).__name__,
                exc,
            )
            return baseline

        rewritten = dict(baseline)
        for item in result.questions:
            if not 0 <= item.index < len(ordered_points):
                continue
            text = self._clean(item.question_text)
            if not text or self._is_internal_code(text):
                continue
            if not text.endswith("?"):
                text = f"{text}?"
            rewritten[ordered_points[item.index]] = text
        return rewritten

    def _rewrite_system_prompt(self) -> str:
        return """
당신은 복지정책 지원 가능성 분석에서 부족한 정보를 사용자에게 묻는 질문 작성자입니다.
입력으로 정확한 판정을 위해 추가 확인이 필요한 항목(missing_point)과 기본 질문(baseline_question)이 주어집니다.

규칙:
1. 각 항목을 사용자가 예/아니오로 답할 수 있는 자연스러운 한국어 질문 1문장으로 바꿉니다.
2. baseline_question의 의미를 유지하되 더 이해하기 쉽고 친근하게 다듬습니다.
3. SERVICE_FIELD, RULE, 영문 코드, JSON 구조 같은 내부 표현은 절대 노출하지 않습니다.
4. 새로운 자격 조건을 지어내지 말고, 주어진 항목이 묻는 내용만 질문합니다.
5. 각 question의 index를 그대로 유지하고, 입력 개수와 같게 반환합니다.
6. 각 질문은 물음표(?)로 끝나는 40~80자 내외의 한 문장으로 작성합니다.
""".strip()

    def is_internal_code(self, value: Any) -> bool:
        text = self._clean(value)
        return bool(text and self._is_internal_code(text))

    def _strip_verdict_suffix(self, text: str) -> str:
        for suffix in ("확인 필요", "충족", "미충족"):
            if text.endswith(suffix):
                return text[: -len(suffix)].strip(" ,")
        return text

    def _looks_like_instruction(self, text: str) -> bool:
        return text.endswith(("해주세요.", "확인해 주세요.", "확인 필요해요."))

    def _is_internal_code(self, text: str) -> bool:
        if re.search(
            r"^SERVICE_FIELD_|^RULE_|^FIELD_|NOT_SUPPORTED|NEEDS_REVIEW|INTERNAL|DEBUG",
            text,
            re.IGNORECASE,
        ):
            return True
        return bool(re.fullmatch(r"[A-Z0-9][A-Z0-9_./\s-]*", text))

    def _clean(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        return text.strip(" ,")

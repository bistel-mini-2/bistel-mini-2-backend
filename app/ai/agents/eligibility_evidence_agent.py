import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.common.ai_status import AssessmentStatus


logger = logging.getLogger(__name__)


class EligibilityEvidenceRewriteItem(BaseModel):
    index: int
    display_text: str = Field(min_length=1, max_length=220)


class EligibilityEvidenceRewriteResult(BaseModel):
    evidences: list[EligibilityEvidenceRewriteItem] = Field(default_factory=list)


@dataclass(frozen=True)
class EligibilityEvidenceContext:
    assessment_status: AssessmentStatus | None = None
    matched_conditions: list[str] = field(default_factory=list)
    missing_conditions: list[str] = field(default_factory=list)
    conflicting_conditions: list[str] = field(default_factory=list)
    manual_check_points: list[str] = field(default_factory=list)


class EligibilityEvidenceAgent:
    """지원 가능성 판정 근거를 사용자에게 보여줄 문장으로 변환한다."""

    _FIELD_LABELS = {
        "age": "연령 조건",
        "childAge": "자녀 연령 조건",
        "child_age": "자녀 연령 조건",
        "stage": "가족 상황 조건",
        "income": "소득 조건",
        "income_level": "소득 조건",
        "income_status": "소득 조건",
        "median_income_percent": "소득 조건",
        "benefit_status": "수급 여부 조건",
        "special": "가구 특성 조건",
        "target": "지원 대상 조건",
        "target_type": "지원 대상 조건",
        "target_context": "지원 대상 조건",
        "debt_status": "채무 상황 조건",
        "caregiver_type": "보호자 유형 조건",
        "pregnancy_or_birth": "임신·출산 조건",
        "eligible_household": "가구 조건",
    }

    def display_text(
        self,
        evidence: dict[str, Any],
        context: EligibilityEvidenceContext,
    ) -> str:
        snippet = str(evidence.get("snippet") or "")
        source_title = str(evidence.get("source_title") or "").strip()
        text = self.clean_user_text(snippet)
        basis = self._basis_text(text=text, source_title=source_title)

        return self._reason_text(
            source_title=source_title,
            basis=basis,
            context=context,
            fallback_text=text,
        )

    async def rewrite_display_texts(
        self,
        evidences: list[dict[str, Any]],
        context: EligibilityEvidenceContext,
        policy_name: str,
        user_conditions: dict[str, Any] | None = None,
        timeout_seconds: float = 20,
    ) -> list[str]:
        fallback_texts = [
            self.clean_user_text(evidence.get("display_text"))
            or self.display_text(evidence=evidence, context=context)
            for evidence in evidences
        ]
        if not evidences or not os.getenv("OPENAI_API_KEY"):
            return fallback_texts

        try:
            from langchain_openai import ChatOpenAI

            structured_llm = ChatOpenAI(
                model="gpt-4o-mini",
                temperature=0,
                max_completion_tokens=800,
                timeout=timeout_seconds,
                max_retries=0,
            ).with_structured_output(EligibilityEvidenceRewriteResult)
            result = await asyncio.wait_for(
                structured_llm.ainvoke(
                    [
                        ("system", self._rewrite_system_prompt()),
                        (
                            "user",
                            json.dumps(
                                {
                                    "policy_name": policy_name,
                                    "assessment_status": (
                                        context.assessment_status.value
                                        if context.assessment_status
                                        else None
                                    ),
                                    "user_conditions": user_conditions or {},
                                    "matched_conditions": context.matched_conditions,
                                    "missing_conditions": context.missing_conditions,
                                    "conflicting_conditions": context.conflicting_conditions,
                                    "manual_check_points": context.manual_check_points,
                                    "evidences": [
                                        {
                                            "index": index,
                                            "source_title": evidence.get("source_title"),
                                            "evidence_role": evidence.get("evidence_role"),
                                            "snippet": self._short_text(
                                                str(evidence.get("snippet") or ""),
                                                600,
                                            ),
                                            "fallback_display_text": fallback_texts[index],
                                        }
                                        for index, evidence in enumerate(evidences)
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
                result = EligibilityEvidenceRewriteResult.model_validate(result)
            if not isinstance(result, EligibilityEvidenceRewriteResult):
                return fallback_texts
        except Exception as exc:
            logger.warning(
                "지원 가능성 판정 근거 LLM 문장화 실패, fallback 사용: %s: %s",
                type(exc).__name__,
                exc,
            )
            return fallback_texts

        rewritten = fallback_texts[:]
        for item in result.evidences:
            if 0 <= item.index < len(rewritten):
                text = self.clean_user_text(item.display_text)
                if text and not self.is_internal_text(text):
                    rewritten[item.index] = self._short_text(text, 220)
        return rewritten

    def clean_user_text(self, value: Any) -> str:
        text = " ".join(str(value or "").split())
        text = re.sub(r"^([A-Za-z_][A-Za-z0-9_]*):\s*", "", text)
        text = text.replace("TRUE", "").replace("FALSE", "").replace("NULL", "")
        return text.strip(" ,")

    def is_internal_text(self, value: Any) -> bool:
        text = str(value or "").strip()
        return bool(
            not text
            or re.search(
                r"^SERVICE_FIELD_|^RULE_|^FIELD_|NOT_SUPPORTED|NEEDS_REVIEW|INTERNAL|DEBUG",
                text,
                re.IGNORECASE,
            )
        )

    def deduplicate_strings(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            text = self.clean_user_text(value)
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def condition_label(self, value: Any) -> str:
        text = self.clean_user_text(value)
        if not text:
            return "해당 조건"

        if any(keyword in text for keyword in ("채무", "빚", "개인회생", "개인파산", "면책")):
            return "채무 상황 조건"
        if any(keyword in text for keyword in ("소득", "중위소득", "수급", "급여")):
            return "소득 조건"
        if any(keyword in text for keyword in ("나이", "연령", "세 이하", "세 이상")):
            return "연령 조건"
        if any(keyword in text for keyword in ("임신", "출산", "영유아", "아동", "청소년", "청년")):
            return "가족 상황 조건"
        if any(keyword in text for keyword in ("장애", "한부모", "다문화", "북한이탈", "난민")):
            return "가구 특성 조건"
        if any(keyword in text for keyword in ("지역", "거주")):
            return "거주 조건"
        if any(keyword in text for keyword in ("피해", "사고", "질병", "재난")):
            return "피해 상황 조건"
        if any(keyword in text for keyword in ("신청", "접수")):
            return "신청 조건"

        fields = self._structured_condition_field_labels(text)
        if fields:
            return fields[0]
        return self._short_text(text, 32)

    def _basis_text(self, text: str, source_title: str) -> str:
        if self._looks_like_structured_condition(text):
            fields = self._structured_condition_field_labels(text)
            return ", ".join(fields[:3]) if fields else "지원 대상과 선정 기준"

        if source_title:
            section = source_title.split(" - ")[-1].strip()
            if section and section != source_title:
                return section

        if any(keyword in text for keyword in ("지원대상", "지원 대상", "대상")):
            return "지원 대상 기준"
        if any(keyword in text for keyword in ("선정기준", "선정 기준", "자격")):
            return "선정 기준"
        if any(keyword in text for keyword in ("소득", "중위소득", "수급")):
            return "소득 기준"
        if any(keyword in text for keyword in ("신청", "접수")):
            return "신청 기준"
        return "정책 원문 기준"

    def _reason_text(
        self,
        source_title: str,
        basis: str,
        context: EligibilityEvidenceContext,
        fallback_text: str,
    ) -> str:
        policy_text = f"{source_title}에서는 " if source_title else "정책 기준에서는 "
        basis_text = basis or "지원 대상과 선정 기준"
        matched = self._condition_summary(context.matched_conditions)
        missing = self._condition_summary(context.missing_conditions)
        conflicts = self._condition_summary(context.conflicting_conditions)
        manual = self._condition_summary(context.manual_check_points)

        if context.assessment_status == AssessmentStatus.LIKELY_MATCH:
            if matched:
                return (
                    f"{policy_text}{basis_text}을 확인했어요. 입력한 정보가 "
                    f"{self._condition_phrase(matched)}과 맞아 지원 가능성이 높다고 판단했어요."
                )
            return (
                f"{policy_text}{basis_text}을 확인했고, 현재 입력한 정보만으로는 "
                "지원 가능성이 높다고 판단했어요."
            )

        if context.assessment_status == AssessmentStatus.NOT_MATCH:
            if missing:
                return (
                    f"{policy_text}{basis_text}을 확인했지만, {self._condition_phrase(missing)}이 "
                    "맞지 않아 지원이 어려울 수 있어요."
                )
            return (
                f"{policy_text}{basis_text}을 확인했지만, 현재 입력한 정보와 "
                "맞지 않는 조건이 있어 지원이 어려울 수 있어요."
            )

        if context.assessment_status == AssessmentStatus.CONFLICTING_PROFILE:
            if conflicts:
                return (
                    f"{policy_text}{basis_text}을 확인했지만, {conflicts} 정보가 "
                    "서로 달라 먼저 입력값을 정리해야 해요."
                )
            return (
                f"{policy_text}{basis_text}을 확인했지만, 입력값이 서로 달라 "
                "먼저 확인이 필요해요."
            )

        if manual:
            return (
                f"{policy_text}{basis_text}을 확인하려면 {self._condition_phrase(manual)} 여부를 "
                "추가로 알려줘야 해요."
            )

        if missing:
            return (
                f"{policy_text}{basis_text}을 판단하려면 {self._condition_phrase(missing)} 정보가 "
                "추가로 필요해요."
            )

        if fallback_text:
            return self._short_text(fallback_text, 220)
        return "정책 원문을 기준으로 지원 가능성을 확인했어요."

    def _looks_like_structured_condition(self, text: str) -> bool:
        return bool(
            re.search(
                r"조건 구조|조건 그룹|field\s*:|operator\s*:|matching_strength|SERVICE_FIELD",
                text,
                re.IGNORECASE,
            )
        )

    def _structured_condition_field_labels(self, text: str) -> list[str]:
        labels: list[str] = []
        for match in re.finditer(r"field\s*:\s*([^,\s]+)", text):
            field = match.group(1).strip()
            label = self._FIELD_LABELS.get(field, field)
            if label and label not in labels:
                labels.append(label)
        return labels

    def _condition_summary(self, conditions: list[str], limit: int = 2) -> str:
        labels = [
            label
            for label in (self.condition_label(condition) for condition in conditions)
            if label and label != "해당 조건" and not self.is_internal_text(label)
        ]
        labels = list(dict.fromkeys(labels))
        if not labels:
            return ""
        return ", ".join(labels[:limit])

    def _condition_phrase(self, value: str) -> str:
        text = self.clean_user_text(value)
        if text.endswith("조건") or text.endswith("기준") or text.endswith("여부"):
            return text
        return f"{text} 조건"

    def _rewrite_system_prompt(self) -> str:
        return """
당신은 복지정책 지원 가능성 분석 결과를 사용자에게 설명하는 문장 작성자입니다.
입력으로 이미 계산된 판정 상태, 사용자 조건, 정책 근거 chunk가 주어집니다.
자격 판정을 새로 하거나 결과를 바꾸지 말고, evidence별 display_text만 자연스러운 한국어 1문장으로 바꾸세요.

규칙:
1. 사용자에게 보여줄 문장만 작성합니다.
2. field, operator, value, matching_strength, SERVICE_FIELD, RULE, JSON 구조 같은 내부 표현은 절대 쓰지 않습니다.
3. 긴 원문을 그대로 복사하지 말고 "채무 상황 조건", "소득 조건", "연령 조건", "가족 상황 조건"처럼 이해하기 쉬운 기준명으로 요약합니다.
4. "확정", "반드시 가능"처럼 단정하지 말고 "가능성이 높아요", "추가 확인이 필요해요", "어려울 수 있어요"처럼 표현합니다.
5. 각 evidence index를 유지하고, 입력 evidence 개수와 같은 순서로 반환합니다.
6. 각 display_text는 80~160자 정도의 한국어 한 문장으로 작성합니다.
""".strip()

    def _short_text(self, value: str, limit: int) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        cut_point = max(text.rfind("다.", 0, limit), text.rfind(".", 0, limit))
        if cut_point > 60:
            return text[: cut_point + 1].strip()
        return text[: limit - 3].rstrip() + "..."

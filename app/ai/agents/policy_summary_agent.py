import asyncio
import json
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.core.config import settings
from app.schemas.ai_contract import EvidenceChunk


class PolicySummaryGeneration(BaseModel):
    summary: str
    evidence: list[str] = Field(default_factory=list)


class PolicySummaryGenerator(Protocol):
    async def generate(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        ...


class LangChainPolicySummaryGenerator:
    def __init__(
        self,
        model: str = "gpt-5.4-mini",
        timeout_seconds: float = 45,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        if not settings.openai_api_key:
            return self._fallback(policy, evidence_chunks)

        from langchain_openai import ChatOpenAI

        llm_kwargs: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
        }
        if settings.openai_api_key:
            llm_kwargs["api_key"] = settings.openai_api_key
        llm = ChatOpenAI(**llm_kwargs)
        structured_llm = llm.with_structured_output(PolicySummaryGeneration)
        messages = [
            (
                "system",
                (
                    "한국 공공정책 상세 정보를 일반 사용자가 이해하기 쉬운 말로 "
                    "요약합니다. 반드시 한국어로 간결한 3줄 요약과 2~3개의 "
                    "짧은 근거 문구를 반환하세요. 근거 문구는 '왜 이렇게 "
                    "요약했는지' 알 수 있게 실제 대상, 금액, 신청 방법, 기간 "
                    "같은 정책별 내용을 한 가지 이상 짚어야 합니다. "
                    "요약과 근거는 제공된 정책 필드와 evidence chunk에만 "
                    "기반해야 합니다. field, operator, source_text, "
                    "condition_json, policy_condition_profile, SERVICE_FIELD, "
                    "RULE, matching_strength 같은 개발자용 내부 표현은 "
                    "절대 출력하지 마세요."
                ),
            ),
            (
                "user",
                self._prompt(policy=policy, evidence_chunks=evidence_chunks),
            ),
        ]
        try:
            result = await asyncio.wait_for(
                structured_llm.ainvoke(messages),
                timeout=self.timeout_seconds,
            )
        except Exception:
            return self._fallback(policy, evidence_chunks)

        if isinstance(result, PolicySummaryGeneration):
            return self._normalize_result(result, policy, evidence_chunks)
        if isinstance(result, dict):
            return self._normalize_result(
                PolicySummaryGeneration.model_validate(result),
                policy,
                evidence_chunks,
            )
        return self._fallback(policy, evidence_chunks)

    def _prompt(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> str:
        fields = {
            "policy": {
                key: policy.get(key)
                for key in (
                    "name",
                    "category",
                    "agency",
                    "benefit_type",
                    "application_status",
                    "benefit_description",
                    "application_method",
                    "application_period_text",
                    "caution",
                )
                if policy.get(key)
            },
            "policy_condition_profile": {
                key: policy.get(f"condition_profile_{key}")
                for key in (
                    "target_summary",
                    "source_text",
                    "source_fields",
                    "confidence",
                    "review_required",
                    "quality_flags",
                )
                if policy.get(f"condition_profile_{key}") is not None
            },
        }
        condition_json = policy.get("condition_profile_json")
        if condition_json:
            fields["policy_condition_profile"]["condition_json"] = condition_json

        evidence_text = "\n\n".join(
            f"[{index + 1}] {chunk.snippet}"
            for index, chunk in enumerate(evidence_chunks[:5])
        )
        return (
            "정책 필드 JSON:\n"
            f"{json.dumps(fields, ensure_ascii=False, default=str)}\n\n"
            f"근거 chunk:\n{evidence_text}"
        )

    def _fallback(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        name = self._clean(policy.get("name")) or "이 정책"
        target = self._clean(
            policy.get("condition_profile_target_summary")
            or policy.get("condition_profile_source_text")
            or policy.get("target_description")
        )
        benefit = self._clean(policy.get("benefit_description"))
        apply_method = self._clean(
            policy.get("application_method")
            or policy.get("application_period_text")
        )

        lines = self._fallback_summary_lines(
            name=name,
            target=target,
            benefit=benefit,
            apply_method=apply_method,
            caution=self._clean(policy.get("caution")),
            evidence_chunks=evidence_chunks,
        )

        fallback_evidence = self._fallback_evidence(
            target=target,
            benefit=benefit,
            apply_method=apply_method,
            caution=self._clean(policy.get("caution")),
            period=self._clean(policy.get("application_period_text")),
            evidence_chunks=evidence_chunks,
        )
        return PolicySummaryGeneration(
            summary="\n".join(lines[:3]),
            evidence=fallback_evidence,
        )

    def _normalize_result(
        self,
        result: PolicySummaryGeneration,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        summary_lines = [
            line.strip()
            for line in result.summary.splitlines()
            if line.strip() and not self._is_generic_summary_line(line)
        ][:3]
        fallback = self._fallback(policy, evidence_chunks)
        if not summary_lines:
            summary_lines = [
                line.strip()
                for line in fallback.summary.splitlines()
                if line.strip()
            ][:3]
        elif len(summary_lines) < 3:
            fallback_lines = [
                line.strip()
                for line in fallback.summary.splitlines()
                if line.strip()
            ]
            for line in fallback_lines:
                if len(summary_lines) >= 3:
                    break
                if line not in summary_lines:
                    summary_lines.append(line)
        summary = "\n".join(summary_lines[:3])
        evidence = [
            self._short(item, 180)
            for item in result.evidence
            if (
                self._clean(item)
                and not self._is_internal_text(item)
                and not self._is_generic_evidence(item)
            )
        ][:3]
        return PolicySummaryGeneration(
            summary=summary,
            evidence=evidence or fallback.evidence,
        )

    def _fallback_summary_lines(
        self,
        *,
        name: str,
        target: str,
        benefit: str,
        apply_method: str,
        caution: str,
        evidence_chunks: list[EvidenceChunk],
    ) -> list[str]:
        lines: list[str] = []
        target_phrase = self._summary_phrase(target, 96)
        benefit_phrase = self._summary_phrase(benefit, 96)
        apply_phrase = self._summary_phrase(apply_method, 90)
        caution_phrase = self._summary_phrase(caution, 90)

        if target_phrase:
            lines.append(f"{target_phrase}이 주요 지원 대상이에요.")

        if benefit_phrase and not any(benefit_phrase in line for line in lines):
            lines.append(self._benefit_summary_line(benefit_phrase))

        if apply_phrase:
            lines.append(f"신청은 {apply_phrase} 내용을 기준으로 진행할 수 있어요.")
        elif caution_phrase:
            lines.append(f"신청 전 {caution_phrase} 내용을 확인해야 해요.")

        if len(lines) < 2:
            chunk_phrase = self._summary_phrase(
                getattr(evidence_chunks[0], "snippet", "") if evidence_chunks else "",
                96,
            )
            if chunk_phrase:
                lines.append(f"정책 원문에는 {chunk_phrase} 내용이 안내되어 있어요.")

        if not lines:
            lines.append(f"{name}은 대상, 혜택, 신청 조건을 공식 안내에서 함께 확인해야 하는 정책이에요.")

        final_checks = (
            "이용 대상, 신청 기간, 제출 서류는 신청 전 함께 확인해 주세요.",
            "거주지나 담당 기관 기준에 따라 세부 절차가 달라질 수 있어요.",
        )
        for line in final_checks:
            if len(lines) >= 3:
                break
            lines.append(line)

        return lines[:3]

    def _fallback_evidence(
        self,
        *,
        target: str,
        benefit: str,
        apply_method: str,
        caution: str,
        period: str,
        evidence_chunks: list[EvidenceChunk],
    ) -> list[str]:
        evidence: list[str] = []
        if target:
            evidence.append(
                f"지원 대상 항목에 {self._quote(self._evidence_phrase(target))} 내용이 있어 대상 정보를 이렇게 정리했어요."
            )
        if benefit:
            evidence.append(
                f"지원 내용 항목에 {self._quote(self._evidence_phrase(benefit))} 내용이 있어 핵심 혜택으로 요약했어요."
            )
        application_basis = apply_method or period or caution
        if application_basis:
            evidence.append(
                f"신청 안내에 {self._quote(self._evidence_phrase(application_basis))} 내용이 있어 신청 전 확인할 부분으로 정리했어요."
            )
        if not evidence and evidence_chunks:
            chunk_text = self._clean(getattr(evidence_chunks[0], "snippet", ""))
            if chunk_text:
                evidence.append(
                    f"정책 문서에 {self._quote(self._evidence_phrase(chunk_text))} 내용이 있어 핵심 정보로 요약했어요."
                )
        if not evidence:
            evidence.append("정책 상세 안내에 있는 대상, 혜택, 신청 정보를 읽기 쉬운 문장으로 정리했어요.")
        return evidence[:3]

    @staticmethod
    def _is_internal_text(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        lowered = text.lower()
        internal_markers = (
            "condition_validation_adjusted",
            "service_field",
            "rule",
            "matching_strength",
            "operator",
            "field",
            "condition_json",
            "source_text",
            "policy_condition_profile",
            "quality_flags",
            "debug",
            "internal",
        )
        return any(marker in lowered for marker in internal_markers)

    @staticmethod
    def _is_generic_evidence(value: Any) -> bool:
        text = str(value or "").strip()
        generic_evidence = {
            "공식 안내의 지원 대상 기준을 바탕으로 정리했어요.",
            "정책 상세 안내에 포함된 지원 내용을 기준으로 요약했어요.",
            "신청 방법과 제출 서류는 공식 안내 문구를 기준으로 정리했어요.",
            "공식 안내의 지원 대상 조건을 기준으로 확인했습니다.",
            "정책 상세 안내에 포함된 지원 내용을 기준으로 정리했습니다.",
            "신청 방법과 신청 전 확인사항은 공식 안내 문구를 기준으로 요약했습니다.",
            "정책 문서의 근거 자료를 바탕으로 요약했습니다.",
            "정책 상세 안내에 포함된 대상, 지원 내용, 신청 조건을 기준으로 정리했습니다.",
        }
        return text in generic_evidence

    @staticmethod
    def _is_generic_summary_line(value: Any) -> bool:
        text = str(value or "").strip()
        return text.endswith("의 핵심 지원 내용을 간단히 정리했어요.")

    @staticmethod
    def _benefit_summary_line(benefit_phrase: str) -> str:
        if benefit_phrase.endswith("지원"):
            return f"{benefit_phrase}해요."
        if benefit_phrase.endswith("제공"):
            return f"{benefit_phrase}해요."
        return f"주요 지원 내용은 {benefit_phrase}이에요."

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _short(cls, value: Any, limit: int) -> str:
        text = " ".join(cls._clean(value).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."

    @classmethod
    def _summary_phrase(cls, value: Any, limit: int) -> str:
        text = " ".join(cls._clean(value).split())
        if not text or cls._is_internal_text(text):
            return ""
        for marker in ("\n", "다.", "요.", ".", ";", "；"):
            if marker in text:
                candidate = text.split(marker, 1)[0].strip()
                if len(candidate) >= 8:
                    text = candidate
                    break
        text = text.strip(" -:;,.")
        return cls._short(text, limit)

    @classmethod
    def _evidence_phrase(cls, value: Any, limit: int = 64) -> str:
        text = cls._clean(value)
        text = text.replace("[", " ").replace("]", " ")
        text = " ".join(text.split())
        for separator in ("。", ".", "\n", " / ", ";"):
            if separator in text:
                text = text.split(separator, 1)[0].strip()
        if len(text) <= limit:
            return text
        return text[:limit].rstrip()

    @staticmethod
    def _quote(value: str) -> str:
        return f"'{value}'" if value else "정책별 안내"


class PolicySummaryAgent:
    def __init__(self, generator: PolicySummaryGenerator | None = None) -> None:
        self.generator = generator or LangChainPolicySummaryGenerator()

    async def summarize(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        return await self.generator.generate(policy, evidence_chunks)

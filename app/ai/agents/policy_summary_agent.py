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
        model: str = "gpt-4o-mini",
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

        llm = ChatOpenAI(model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(PolicySummaryGeneration)
        messages = [
            (
                "system",
                (
                    "한국 공공정책 상세 정보를 일반 사용자가 이해하기 쉬운 말로 "
                    "요약합니다. 반드시 한국어로 간결한 3줄 요약과 2~3개의 "
                    "짧은 근거 문구를 반환하세요. 요약과 근거는 제공된 정책 "
                    "필드와 evidence chunk에만 기반해야 합니다. 지원 조건과 "
                    "대상 판단은 policy_condition_profile.source_text와 "
                    "condition_json을 1차 기준으로 삼고, 기존 policy_detail "
                    "필드는 보조 문맥으로만 사용하세요."
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

        lines = [f"{name}의 핵심 지원 내용을 간단히 정리했어요."]
        if target:
            lines.append(f"대상: {self._short(target, 90)}")
        if benefit:
            lines.append(f"지원 내용: {self._short(benefit, 90)}")
        if len(lines) < 3 and apply_method:
            lines.append(f"신청: {self._short(apply_method, 90)}")
        fallback_lines = (
            "지원 조건은 정책 원문 기준으로 확인이 필요해요.",
            "신청 전 대상, 기간, 제출 서류를 다시 확인해 주세요.",
            "자세한 내용은 정책 상세 안내와 담당 기관 공지를 참고해 주세요.",
        )
        for line in fallback_lines:
            if len(lines) >= 3:
                break
            lines.append(line)

        fallback_evidence = [
            self._short(chunk.snippet, 120)
            for chunk in evidence_chunks[:3]
            if self._clean(chunk.snippet)
        ]
        if not fallback_evidence:
            fallback_evidence = [
                item
                for item in (
                    self._short(policy.get("condition_profile_source_text"), 120),
                    self._short(target, 120),
                    self._short(benefit, 120),
                    self._short(apply_method, 120),
                )
                if item
            ][:3]
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
            if line.strip()
        ][:3]
        fallback = self._fallback(policy, evidence_chunks)
        for line in fallback.summary.splitlines():
            if len(summary_lines) >= 3:
                break
            value = line.strip()
            if value:
                summary_lines.append(value)
        summary = "\n".join(summary_lines[:3])
        evidence = [
            self._short(item, 160)
            for item in result.evidence
            if self._clean(item)
        ][:3]
        return PolicySummaryGeneration(
            summary=summary,
            evidence=evidence or fallback.evidence,
        )

    @staticmethod
    def _clean(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _short(cls, value: Any, limit: int) -> str:
        text = " ".join(cls._clean(value).split())
        if len(text) <= limit:
            return text
        return text[: limit - 3].rstrip() + "..."


class PolicySummaryAgent:
    def __init__(self, generator: PolicySummaryGenerator | None = None) -> None:
        self.generator = generator or LangChainPolicySummaryGenerator()

    async def summarize(
        self,
        policy: dict[str, Any],
        evidence_chunks: list[EvidenceChunk],
    ) -> PolicySummaryGeneration:
        return await self.generator.generate(policy, evidence_chunks)

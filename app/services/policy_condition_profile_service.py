import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from fastapi import Depends
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from app.common.psycopg_pool_conf import psycopg_pool
from app.core.config import settings
from app.repositories.policy_condition_profile_repository import (
    PolicyConditionProfileRepository,
)
from app.schemas.policy_condition_profile_schema import (
    PolicyConditionProfileIngestItem,
    PolicyConditionProfileIngestResponse,
    PolicyConditionProfileSkipItem,
)


class _PolicyConditionExtractionModel(BaseModel):
    target_summary: str | None = Field(
        default=None,
        description="정책 지원대상과 핵심 자격 조건 요약",
    )
    condition_tree: dict[str, Any] = Field(
        default_factory=dict,
        description="AND/OR 관계를 보존한 정책 자격 조건 트리",
    )
    exclusions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="지원 제외 또는 중복 제한 조건",
    )
    unknowns: list[dict[str, Any]] = Field(
        default_factory=list,
        description="원문만으로 구조화하기 어려운 조건",
    )
    confidence: float | None = Field(
        default=None,
        ge=0,
        le=1,
        description="전체 구조화 결과 신뢰도",
    )
    review_required: bool = Field(
        default=False,
        description="수동 검토 필요 여부",
    )
    quality_flags: list[str] = Field(
        default_factory=list,
        description="모호함, 누락, 복합조건 등 품질 플래그",
    )


ConditionExtractor = Callable[
    [dict[str, Any], str, list[str]],
    Awaitable[_PolicyConditionExtractionModel],
]


class PolicyConditionProfileService:
    def __init__(
        self,
        repository: type[PolicyConditionProfileRepository]
        | PolicyConditionProfileRepository = PolicyConditionProfileRepository,
        extractor: ConditionExtractor | None = None,
    ) -> None:
        self.logger = logging.getLogger(f"{__name__}.PolicyConditionProfileService")
        self.repository = repository
        self.extractor = extractor

    async def ingest_condition_profiles(
        self,
        limit: int = 10,
        overwrite: bool = False,
    ) -> PolicyConditionProfileIngestResponse:
        items: list[PolicyConditionProfileIngestItem] = []
        skipped: list[PolicyConditionProfileSkipItem] = []
        failed: list[dict[str, str]] = []

        async with psycopg_pool.connection() as conn:
            await self.repository.ensure_schema(conn)
            targets = await self.repository.find_profile_targets(
                conn=conn,
                limit=limit,
                overwrite=overwrite,
            )

            for target in targets:
                source_text, source_fields = self._build_source_text(target)
                if not self._has_condition_source(source_fields):
                    skipped.append(
                        PolicyConditionProfileSkipItem(
                            policy_id=target["policy_id"],
                            policy_code=target["policy_code"],
                            policy_name=target["policy_name"],
                            reason="조건 해석에 사용할 정책 원문이 없습니다.",
                        )
                    )
                    continue

                try:
                    extraction = await self._extract_conditions(
                        target=target,
                        source_text=source_text,
                        source_fields=source_fields,
                    )
                    condition_json = self._condition_json(
                        extraction=extraction,
                        target=target,
                    )
                    async with conn.transaction():
                        condition_profile_id = await self.repository.upsert_profile(
                            conn=conn,
                            policy_id=target["policy_id"],
                            condition_json=condition_json,
                            target_summary=extraction.target_summary,
                            confidence=extraction.confidence,
                            review_required=extraction.review_required,
                            quality_flags=extraction.quality_flags,
                            source_text=source_text,
                            source_fields=source_fields,
                        )

                    items.append(
                        PolicyConditionProfileIngestItem(
                            policy_id=target["policy_id"],
                            policy_code=target["policy_code"],
                            policy_name=target["policy_name"],
                            condition_profile_id=condition_profile_id,
                            target_summary=extraction.target_summary,
                            confidence=extraction.confidence,
                            review_required=extraction.review_required,
                            quality_flags=extraction.quality_flags,
                        )
                    )
                except Exception as exc:
                    self.logger.exception("정책 조건 profile 생성 중 오류 발생")
                    failed.append(
                        {
                            "policy_id": str(target.get("policy_id")),
                            "policy_code": str(target.get("policy_code")),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )

        return PolicyConditionProfileIngestResponse(
            requested_count=len(targets),
            completed_count=len(items),
            skipped_count=len(skipped),
            failed_count=len(failed),
            items=items,
            skipped=skipped,
            failed=failed,
        )

    async def _extract_conditions(
        self,
        *,
        target: dict[str, Any],
        source_text: str,
        source_fields: list[str],
    ) -> _PolicyConditionExtractionModel:
        if self.extractor is not None:
            return await self.extractor(target, source_text, source_fields)

        llm = self._llm().with_structured_output(
            _PolicyConditionExtractionModel,
            method="function_calling",
        )
        response = await llm.ainvoke(
            [
                SystemMessage(content=self._system_prompt()),
                HumanMessage(
                    content=self._user_prompt(
                        target=target,
                        source_text=source_text,
                        source_fields=source_fields,
                    )
                ),
            ]
        )
        return response

    def _llm(self) -> ChatOpenAI:
        kwargs = {}
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            **kwargs,
        )

    def _build_source_text(self, target: dict[str, Any]) -> tuple[str, list[str]]:
        field_labels = [
            ("policy_name", "정책명"),
            ("main_category", "대분류"),
            ("sub_category", "생애주기/하위분류"),
            ("benefit_type", "급여유형"),
            ("life_array", "OpenAPI 생애주기"),
            ("target_individual_array", "OpenAPI 대상특성"),
            ("interest_theme_array", "OpenAPI 관심주제"),
            ("raw_target_detail", "OpenAPI 지원대상"),
            ("raw_selection_criteria", "OpenAPI 선정기준"),
            ("target_description", "DB 지원대상"),
            ("easy_summary", "DB 쉬운 요약"),
            ("raw_outline", "OpenAPI 개요"),
            ("raw_benefit_content", "OpenAPI 지원내용"),
            ("benefit_description", "DB 지원내용"),
            ("application_period_text", "DB 신청기간"),
            ("caution", "DB 유의사항"),
        ]
        parts: list[str] = []
        source_fields: list[str] = []
        seen_values: set[str] = set()

        for field_name, label in field_labels:
            value = self._clean_text(target.get(field_name))
            if not value or value in seen_values:
                continue
            seen_values.add(value)
            source_fields.append(field_name)
            parts.append(f"[{label}]\n{value}")

        return "\n\n".join(parts), source_fields

    def _has_condition_source(self, source_fields: list[str]) -> bool:
        metadata_fields = {
            "policy_name",
            "main_category",
            "sub_category",
            "benefit_type",
        }
        return any(field_name not in metadata_fields for field_name in source_fields)

    def _condition_json(
        self,
        *,
        extraction: _PolicyConditionExtractionModel,
        target: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "policy_id": target["policy_id"],
            "policy_code": target["policy_code"],
            "policy_name": target["policy_name"],
            "target_summary": extraction.target_summary,
            "condition_tree": extraction.condition_tree,
            "exclusions": extraction.exclusions,
            "unknowns": extraction.unknowns,
            "review_required": extraction.review_required,
            "confidence": extraction.confidence,
            "quality_flags": extraction.quality_flags,
        }

    def _system_prompt(self) -> str:
        return """
당신은 복지 정책 원문을 추천/지원 가능성 판정용 조건 JSON으로 구조화하는 백엔드 Agent입니다.
반드시 제공된 원문 안의 내용만 사용하세요. 원문에 없는 기준값을 추측하거나 보완하지 마세요.

목표:
- 정책 지원대상과 선정기준을 AND/OR 관계가 보존된 condition_tree로 정리합니다.
- 모든 조건에는 가능하면 type, field, operator, value, source_text, confidence를 포함합니다.
- 제외 조건이나 중복 제한은 exclusions에 분리합니다.
- 원문이 모호하거나 기준값이 없으면 unknowns에 넣고 review_required를 true로 둡니다.

condition_tree 권장 형식:
{
  "operator": "AND" 또는 "OR",
  "conditions": [
    {
      "group_key": "income",
      "operator": "AND",
      "conditions": [
        {
          "type": "income",
          "field": "median_income",
          "operator": "LTE",
          "value": {"percent": 32},
          "source_text": "기준중위소득 32% 이하",
          "confidence": 0.95
        }
      ]
    }
  ]
}

주의:
- "임산부 또는 34세 이하"처럼 선택 조건이면 반드시 OR 그룹으로 표현합니다.
- "생계급여 수급가구 중 ..."처럼 선행 자격과 대상 조건이 결합되면 전체는 AND로 표현합니다.
- OpenAPI의 category, sub_category, lifeArray, trgterIndvdlArray는 참고 정보일 뿐입니다.
- 실제 조건 판단은 지원대상, 선정기준, target_description 원문을 우선합니다.
- OpenAPI 분류 정보와 지원대상/선정기준 원문이 충돌하면 지원대상/선정기준 원문을 기준으로 condition_tree를 작성합니다.
- "저소득층"처럼 정확한 기준이 없으면 임의의 중위소득 비율을 만들지 말고 unknowns에 남깁니다.
- 답변 필드의 모든 자연어는 한국어로 작성합니다.
""".strip()

    def _user_prompt(
        self,
        *,
        target: dict[str, Any],
        source_text: str,
        source_fields: list[str],
    ) -> str:
        return f"""
정책 식별자:
- policy_id: {target["policy_id"]}
- policy_code: {target["policy_code"]}
- policy_name: {target["policy_name"]}

사용한 원문 필드:
{", ".join(source_fields)}

정책 원문:
{source_text}
""".strip()

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def get_policy_condition_profile_service() -> PolicyConditionProfileService:
    return PolicyConditionProfileService()


PolicyConditionProfileServiceDep = Annotated[
    PolicyConditionProfileService,
    Depends(get_policy_condition_profile_service),
]

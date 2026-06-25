import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field as dataclass_field
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


# condition_tree leaf에 허용되는 표준 operator enum.
STANDARD_OPERATORS = {"EXISTS", "EQ", "IN", "LTE", "GTE", "LT", "GT", "UNKNOWN"}

# LLM이 흔들리며 생성하는 operator 표현을 표준 enum으로 정규화하는 매핑.
_OPERATOR_ALIASES = {
    "EXISTS": "EXISTS",
    "EXIST": "EXISTS",
    "PRESENT": "EXISTS",
    "HAS": "EXISTS",
    "EQ": "EQ",
    "EQUAL": "EQ",
    "EQUALS": "EQ",
    "IS": "EQ",
    "==": "EQ",
    "=": "EQ",
    "IN": "IN",
    "INCLUDE": "IN",
    "INCLUDES": "IN",
    "ONE_OF": "IN",
    "ANY_OF": "IN",
    "ANYOF": "IN",
    "LTE": "LTE",
    "LE": "LTE",
    "<=": "LTE",
    "GTE": "GTE",
    "GE": "GTE",
    ">=": "GTE",
    "LT": "LT",
    "<": "LT",
    "GT": "GT",
    ">": "GT",
    "UNKNOWN": "UNKNOWN",
    "UNDEFINED": "UNKNOWN",
    "NULL": "UNKNOWN",
}

# condition_tree leaf에 부여하는 매칭 강도.
STRENGTH_HARD = "hard"
STRENGTH_SOFT = "soft"
STRENGTH_FOLLOW_UP = "follow_up"

# 1단계 hard: 선택식 UI에서 직접 받는 확정 매칭 조건.
_HARD_FIELDS = {
    "stage",
    "child_age",
    "income_status",
    "median_income_percent",
    "region",
    "household_type",
    "special_condition",
    "pregnancy_status",
    "household_member_age",
    "age",
}

# 2단계 soft: 자연어에서 추출 가능, 추천 점수/후보 보정에 활용(확정 조건 아님).
_SOFT_FIELDS = {
    "employment_status",
    "worker_status",
    "employee_status",
    "insurance_status",
    "program_participation",
    "benefit_overlap",
}

# 2단계 follow_up: 판단에 중요하지만 입력에 없으면 추가 확인이 필요한 조건.
_FOLLOW_UP_FIELDS = {
    "accident_victim_status",
    "debt_status",
    "bankruptcy_status",
    "legal_consultation_need",
    "environmental_damage_type",
}

# 하위 호환용 별칭(과거 코드/테스트가 참조).
STANDARD_FIELDS = _HARD_FIELDS

# 3단계 unsupported: 국적/체류/난민 등 식별 조건(canonical field로 보존).
_OUT_OF_SCOPE_FIELDS = {
    "nationality_status",
    "residency_status",
    "refugee_status",
    "naturalization_status",
    "visa_status",
    "legal_stay_status",
}

# 임의 field 이름에서 식별 조건을 탐지하기 위한 키워드 → canonical 매핑.
_OUT_OF_SCOPE_KEYWORDS = {
    "foreign": "nationality_status",
    "nationality": "nationality_status",
    "refugee": "refugee_status",
    "naturaliz": "naturalization_status",
    "residency": "residency_status",
    "visa": "visa_status",
    "legal_stay": "legal_stay_status",
}

# 3단계 unsupported: 상세 장애/직업(농업인) 등 자연어로도 안정 처리 어려운 조건.
_DETAILED_DISABILITY_FIELDS = {
    "disability_type",
    "detailed_disability_type",
    "detailed_disability_grade",
    "disability_grade",
}
_UNSUPPORTED_CONDITION_FIELDS = {
    "farmer_status",
    "occupation_detail",
}
_UNSUPPORTED_CONDITION_KEYWORDS = ("farmer",)

# 의미가 명확한 임의 field 이름만 표준 field(필요 시 operator/value 보정)로 normalize.
# 의미가 모호하거나 서비스가 수집하지 않는 field(debt_status, occupation_detail 등)는
# 여기에 추가하지 않는다 → 표준 field가 아니므로 unknowns로 분리된다.
_FIELD_NORMALIZATION = {
    # 대상 유형 → 생애주기(stage)
    "young_children": {"field": "stage"},
    "infant": {"field": "stage"},
    "newborn": {"field": "stage"},
    # 고용/근로 상태 → employment_status(soft)
    "occupation": {"field": "employment_status"},
    "worker_status": {"field": "employment_status"},
    "employee_status": {"field": "employment_status"},
    "working_status": {"field": "employment_status"},
    # 연령 경계
    "under_34": {
        "field": "household_member_age",
        "operator": "LTE",
        "value": {"years": 34},
    },
    # 소득 상태
    "basic_living_security": {
        "field": "income_status",
        "value": ["basic_livelihood_recipient"],
    },
    "basic_livelihood_security": {
        "field": "income_status",
        "value": ["basic_livelihood_recipient"],
    },
    "livelihood_recipient": {
        "field": "income_status",
        "value": ["basic_livelihood_recipient"],
    },
    "near_basic_living_security": {
        "field": "income_status",
        "value": ["near_poverty_class"],
    },
    "near_poverty": {
        "field": "income_status",
        "value": ["near_poverty_class"],
    },
    "median_income": {"field": "median_income_percent"},
}

# exclusions 오판을 거르기 위한 문구 사전.
_POSITIVE_PHRASES = (
    "지원 가능",
    "지원가능",
    "포함 가능",
    "포함가능",
    "대상에 해당",
    "대상에해당",
    "신청 가능",
    "신청가능",
)
_EXCLUSION_NEGATIVE_KEYWORDS = (
    "제외",
    "불가",
    "정지",
    "상실",
    "박탈",
    "중단",
    "않음",
    "않는",
    "지원하지",
)

# condition_json 각 버킷에 분류된 사유를 표준 코드로 남긴다(#154 변환에서 활용).
REASON_FIELD_NOT_SUPPORTED = "SERVICE_FIELD_NOT_SUPPORTED"
REASON_DETAILED_DISABILITY = "DETAILED_DISABILITY_TYPE_NOT_COLLECTED"
REASON_EXPLICIT_EXCLUSION = "EXPLICIT_EXCLUSION"
REASON_CALCULATION = "CALCULATION_RULE_NOT_TARGET_EXCLUSION"
REASON_POSITIVE = "POSITIVE_STATEMENT_NOT_EXCLUSION"

# validation layer가 leaf condition을 옮기거나 제거할 때 남기는 사유 코드.
REASON_INVALID_FIELD_VALUE = "INVALID_FIELD_VALUE"
REASON_BIRTH_EVENT_NOT_STAGE = "BIRTH_EVENT_NOT_STAGE"
REASON_ASSET_NOT_REGION = "ASSET_CRITERION_NOT_REGION"

# validation 결과를 quality_flags에 남기는 코드.
QUALITY_VALIDATION_ADJUSTED = "condition_validation_adjusted"
QUALITY_VALIDATION_ERROR = "validation_error"
QUALITY_EMPTY_CONDITION_TREE = "empty_condition_tree"

# stage(생애주기) 표준 허용값. UI/Condition Agent가 실제로 비교하는 값만 둔다.
_STAGE_ALLOWED_VALUES = {"pregnant", "newborn", "infant", "child", "youth"}
# 출산/유산/사산 등 "이벤트"는 생애주기(stage)가 아니므로 stage에 저장하지 않는다.
_STAGE_BIRTH_EVENT_VALUES = {
    "birth",
    "childbirth",
    "delivery",
    "miscarriage",
    "stillbirth",
    "abortion",
}

# special_condition(UI 특수상황) 표준 허용 enum. 정확히 일치할 때만 hard로 저장한다.
_SPECIAL_CONDITION_ALLOWED_VALUES = {
    "single_parent_or_grandparent",
    "multicultural_or_defector",
    "disabled_household",
    "multichild",
    "low_income",
}
# special_condition으로 절대 볼 수 없는 원문 신호. enum 값이 형식상 유효해도(농업인→
# multichild 같은 억지 매핑) 원문에 이 키워드가 있으면 hard에서 분리한다.
_SPECIAL_CONDITION_MISMATCH_KEYWORDS = (
    "농업",
    "농어업",
    "어업",
    "임업",
    "축산",
    "학대",
    "성폭력",
    "성폭행",
    "가정폭력",
    "실직",
    "실업",
    "긴급돌봄",
    "위기사유",
    "중한 질병",
    "중대질병",
    "질병",
)

# child_age dict value에서 나이로 인정하는 키.
_CHILD_AGE_NUMERIC_KEYS = {
    "years",
    "year",
    "age",
    "value",
    "min",
    "max",
    "from",
    "to",
    "gte",
    "lte",
    "lt",
    "gt",
}
# child_age로 저장하면 안 되는 임상 수치(출생체중/재태기간) 신호 키워드.
_CHILD_AGE_CLINICAL_KEYWORDS = (
    "체중",
    "그램",
    "kg",
    "재태",
    "주수",
    "임신주",
    "출생주",
    "gram",
    "weight",
    "gestation",
)
# region이 아니라 자산/재산 기준임을 알려주는 신호 키워드.
_REGION_ASSET_KEYWORDS = ("자산", "재산", "대도시", "중소도시", "농어촌")
_AMOUNT_PATTERN = re.compile(r"\d+\s*(억|만원|만 원|원|천만)")

# 원문 입력 길이 상한. 과도하게 긴(또는 반복적인) 원문은 LLM이 함수 인자 JSON을
# 폭주 생성(finish_reason=length)하게 만들어 추출 자체가 실패하므로 캡을 둔다.
# 조건 관련 필드가 앞쪽에 오도록 정렬돼 있어, 초과분은 주로 지원내용/유의사항 등
# 조건 비핵심 원문에서 잘린다.
_MAX_FIELD_CHARS = 2500
_MAX_SOURCE_TEXT_CHARS = 8000
_TRUNCATION_MARKER = "…(원문 일부 생략)"


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
    ignored_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "제외 조건이 아니거나(예: '지원 가능' 문구) 서비스가 수집하지 않아 "
            "active condition_tree에 넣지 않는 참고 조건"
        ),
    )
    unsupported_conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "정책의 핵심 대상이 국적/체류/난민/귀화 등 서비스 범위 밖 조건인 경우 "
            "보존하는 조건. 채워지면 review_required를 true로 둔다."
        ),
    )
    special_notes: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "'가구원 수 산출에서 제외', '소득 산정에서 제외'처럼 대상 제외가 아닌 "
            "산정·산출 방식 관련 특이 문구"
        ),
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


@dataclass
class _NormalizedConditions:
    condition_tree: dict[str, Any]
    exclusions: list[dict[str, Any]]
    ignored_conditions: list[dict[str, Any]]
    unsupported_conditions: list[dict[str, Any]]
    special_notes: list[dict[str, Any]]
    unknowns: list[dict[str, Any]]
    review_required: bool
    follow_up_required: bool = False
    quality_flags: list[Any] = dataclass_field(default_factory=list)


@dataclass
class _Buckets:
    """condition_tree normalize 중 분리되는 비-active 조건들을 모은다."""

    exclusions: list[dict[str, Any]] = dataclass_field(default_factory=list)
    unsupported: list[dict[str, Any]] = dataclass_field(default_factory=list)
    unknowns: list[dict[str, Any]] = dataclass_field(default_factory=list)
    follow_up_required: bool = False


@dataclass
class _ValidationState:
    """validation 단계에서 leaf를 옮기거나 제거한 내역을 모은다."""

    unsupported: list[dict[str, Any]] = dataclass_field(default_factory=list)
    unknowns: list[dict[str, Any]] = dataclass_field(default_factory=list)
    # 값/필드를 교정해 tree에 그대로 남긴 경우(비파괴적).
    adjusted: bool = False
    # leaf를 hard 조건에서 제거/이동한 경우(파괴적).
    removed: bool = False


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
                    normalized = self._normalize_extraction(extraction)
                    normalized = self._validate_conditions(
                        normalized, policy_code=target.get("policy_code")
                    )
                    condition_json = self._condition_json(
                        extraction=extraction,
                        normalized=normalized,
                        target=target,
                    )
                    async with conn.transaction():
                        condition_profile_id = await self.repository.upsert_profile(
                            conn=conn,
                            policy_id=target["policy_id"],
                            condition_json=condition_json,
                            target_summary=extraction.target_summary,
                            confidence=extraction.confidence,
                            review_required=normalized.review_required,
                            quality_flags=normalized.quality_flags,
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
                            review_required=normalized.review_required,
                            quality_flags=normalized.quality_flags,
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
            include_raw=True,
        )
        result = await llm.ainvoke(
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
        parsed = result.get("parsed")
        if parsed is not None:
            return parsed

        detail = self._describe_extraction_failure(result)
        raise ValueError(f"{detail} (입력 원문 {len(source_text)}자)")

    def _describe_extraction_failure(self, result: dict[str, Any]) -> str:
        parsing_error = result.get("parsing_error")
        raw = result.get("raw")
        tool_calls = getattr(raw, "tool_calls", None) or []
        finish_reason = ""
        raw_text = ""
        if raw is not None:
            metadata = getattr(raw, "response_metadata", {}) or {}
            finish_reason = metadata.get("finish_reason", "")
            raw_text = self._clean_text(getattr(raw, "content", "")) or ""

        if parsing_error is not None:
            detail = (
                f"구조화 출력 파싱 실패: {parsing_error} "
                f"(finish_reason={finish_reason or 'unknown'})"
            )
        elif not tool_calls:
            detail = (
                "LLM이 조건 추출 함수를 호출하지 않았습니다 "
                f"(finish_reason={finish_reason or 'unknown'})."
            )
            if raw_text:
                detail += f" 응답 텍스트: {raw_text[:200]}"
        else:
            detail = (
                "LLM이 구조화된 조건 추출 결과를 반환하지 못했습니다 "
                f"(finish_reason={finish_reason or 'unknown'})."
            )
        return detail

    def _llm(self) -> ChatOpenAI:
        kwargs = {}
        if settings.openai_api_key:
            kwargs["api_key"] = settings.openai_api_key
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            max_completion_tokens=8192,
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
            value = self._truncate(value, _MAX_FIELD_CHARS)
            source_fields.append(field_name)
            parts.append(f"[{label}]\n{value}")

        source_text = self._truncate(
            "\n\n".join(parts), _MAX_SOURCE_TEXT_CHARS
        )
        return source_text, source_fields

    def _truncate(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rstrip() + _TRUNCATION_MARKER

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
        normalized: _NormalizedConditions,
        target: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "policy_id": target["policy_id"],
            "policy_code": target["policy_code"],
            "policy_name": target["policy_name"],
            "target_summary": extraction.target_summary,
            "condition_tree": normalized.condition_tree,
            "exclusions": normalized.exclusions,
            "ignored_conditions": normalized.ignored_conditions,
            "unsupported_conditions": normalized.unsupported_conditions,
            "special_notes": normalized.special_notes,
            "unknowns": normalized.unknowns,
            "review_required": normalized.review_required,
            "follow_up_required": normalized.follow_up_required,
            "confidence": extraction.confidence,
            "quality_flags": normalized.quality_flags,
        }

    def _normalize_extraction(
        self,
        extraction: _PolicyConditionExtractionModel,
    ) -> _NormalizedConditions:
        """LLM 추출 결과를 서비스 조건 체계에 맞게 결정적으로 보정한다.

        - hard/soft/follow_up tier 조건은 condition_tree에 남기고 matching_strength를 부여한다.
        - 국적/체류/난민·상세장애·농업인 등 미지원 조건은 unsupported_conditions로 분리한다.
        - 표현 불가/미상(field unknown 등) 조건은 unknowns로 분리한다.
        - operator EXCLUDES는 exclusions 구조로 분리하고 enum을 표준화한다.
        - '지원 가능'(긍정)/'산정·산출 제외'(산정 방식) 문구는 exclusions에서 분리한다.
        - ignored에 잘못 들어간 명시적 '제외' 문장은 exclusions로 되돌린다.
        """
        exclusions_in = [dict(item) for item in extraction.exclusions]
        unknowns = [dict(item) for item in extraction.unknowns]
        ignored = [dict(item) for item in extraction.ignored_conditions]
        unsupported = [dict(item) for item in extraction.unsupported_conditions]
        special_notes = [dict(item) for item in extraction.special_notes]
        quality_flags = list(extraction.quality_flags)
        review_required = extraction.review_required

        buckets = _Buckets(unsupported=unsupported, unknowns=unknowns)
        unknowns_before = len(unknowns)
        unsupported_before = len(unsupported)

        tree = self._normalize_tree_node(extraction.condition_tree, buckets=buckets)
        if tree is None:
            tree = {}

        kept_exclusions: list[dict[str, Any]] = []
        for item in exclusions_in + buckets.exclusions:
            kind = self._classify_exclusion(self._exclusion_text(item))
            if kind == "calculation":
                special_notes.append(self._with_reason(item, REASON_CALCULATION))
                review_required = True
            elif kind == "not_exclusion":
                ignored.append(self._with_reason(item, REASON_POSITIVE))
            else:
                kept_exclusions.append(self._sanitize_exclusion(item))

        # ignored에 잘못 분류된 명시적 제외 문장을 exclusions로 되돌린다.
        final_ignored: list[dict[str, Any]] = []
        for item in ignored:
            text = self._exclusion_text(item)
            if self._is_calculation_exclusion(text):
                special_notes.append(self._with_reason(item, REASON_CALCULATION))
                review_required = True
            elif self._has_explicit_exclusion(text) and not self._has_positive(text):
                kept_exclusions.append(
                    self._sanitize_exclusion(item, reason=REASON_EXPLICIT_EXCLUSION)
                )
            else:
                final_ignored.append(item)

        follow_up_required = buckets.follow_up_required
        if len(unsupported) > unsupported_before:
            self._add_flag(quality_flags, "unsupported_condition_present")
        if len(unknowns) > unknowns_before:
            self._add_flag(quality_flags, "unknown_condition_present")
        if follow_up_required:
            self._add_flag(quality_flags, "follow_up_required")
        if (
            unsupported
            or len(unknowns) > unknowns_before
            or follow_up_required
        ):
            review_required = True

        return _NormalizedConditions(
            condition_tree=tree,
            exclusions=kept_exclusions,
            ignored_conditions=final_ignored,
            unsupported_conditions=unsupported,
            special_notes=special_notes,
            unknowns=unknowns,
            review_required=review_required,
            follow_up_required=follow_up_required,
            quality_flags=quality_flags,
        )

    def _validate_conditions(
        self,
        normalized: _NormalizedConditions,
        *,
        policy_code: Any = None,
    ) -> _NormalizedConditions:
        """normalize 이후, DB 저장 전 condition_tree leaf의 field/value 계약을 검증한다.

        - 값이 field 타입에 맞으면 in-place로 교정해 tree에 남긴다(예: "3세"→3,
          income_status percent dict→median_income_percent).
        - hard 조건으로 저장할 수 없는 leaf는 unsupported_conditions/unknowns로 옮긴다.
        - 비슷해 보이는 enum에 억지 매핑된 값(농업인→multichild 등)을 제거한다.
        - 보정/이동이 있으면 quality_flags와 review_required를 보정한다.
        """
        state = _ValidationState(
            unsupported=list(normalized.unsupported_conditions),
            unknowns=list(normalized.unknowns),
        )

        tree = self._validate_tree_node(normalized.condition_tree, state)
        if tree is None:
            tree = {}

        quality_flags = list(normalized.quality_flags)
        review_required = normalized.review_required

        if state.adjusted:
            self._add_flag(quality_flags, QUALITY_VALIDATION_ADJUSTED)
        if state.removed:
            self._add_flag(quality_flags, QUALITY_VALIDATION_ERROR)
            review_required = True
        if state.unsupported or state.unknowns:
            review_required = True
        if self._is_empty_tree(tree):
            self._add_flag(quality_flags, QUALITY_EMPTY_CONDITION_TREE)
            review_required = True

        if state.adjusted or state.removed:
            self.logger.info(
                "condition validation 보정: policy_code=%s adjusted=%s removed=%s "
                "unsupported=%d unknowns=%d",
                policy_code,
                state.adjusted,
                state.removed,
                len(state.unsupported),
                len(state.unknowns),
            )

        return _NormalizedConditions(
            condition_tree=tree,
            exclusions=normalized.exclusions,
            ignored_conditions=normalized.ignored_conditions,
            unsupported_conditions=state.unsupported,
            special_notes=normalized.special_notes,
            unknowns=state.unknowns,
            review_required=review_required,
            follow_up_required=normalized.follow_up_required,
            quality_flags=quality_flags,
        )

    def _validate_tree_node(
        self,
        node: Any,
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None

        conditions = node.get("conditions")
        if isinstance(conditions, list):
            new_conditions: list[dict[str, Any]] = []
            for child in conditions:
                validated = self._validate_tree_node(child, state)
                if validated is not None:
                    new_conditions.append(validated)
            if not new_conditions:
                return None
            result = dict(node)
            result["conditions"] = new_conditions
            return result

        return self._validate_leaf(node, state)

    def _validate_leaf(
        self,
        node: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        leaf = dict(node)
        field_key = self._field_key(leaf.get("field"))
        validator = {
            "stage": self._validate_stage,
            "child_age": self._validate_child_age,
            "income_status": self._validate_income_status,
            "median_income_percent": self._validate_median_income,
            "special_condition": self._validate_special_condition,
            "region": self._validate_region,
        }.get(field_key or "")
        if validator is None:
            return leaf
        return validator(leaf, state)

    def _validate_stage(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        operator = str(leaf.get("operator", "")).strip().upper()
        value = leaf.get("value")

        tokens = self._as_token_list(value)
        normalized_tokens = [str(t).strip().lower() for t in tokens if t is not None]

        birth = [t for t in normalized_tokens if t in _STAGE_BIRTH_EVENT_VALUES]
        valid = [t for t in normalized_tokens if t in _STAGE_ALLOWED_VALUES]

        if birth:
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="birth_event", reason=REASON_BIRTH_EVENT_NOT_STAGE
                )
            )
            state.removed = True

        # 값이 비어있고 EXISTS 등으로 stage만 표시된 경우(예: stage EXISTS None)는 제거.
        if not valid:
            if not birth:
                state.removed = True
                if not self._is_effectively_empty(value):
                    # 숫자/이벤트 외 임의 값은 hard로 신뢰 불가 → unknowns.
                    state.unknowns.append(
                        {
                            "field": "stage",
                            "value": value,
                            "source_text": leaf.get("source_text"),
                            "reason": REASON_INVALID_FIELD_VALUE,
                        }
                    )
            return None

        out = dict(leaf)
        if len(valid) == 1:
            out["operator"] = "EQ" if operator not in ("IN",) else "IN"
            out["value"] = valid[0]
        else:
            out["operator"] = "IN"
            out["value"] = valid
        if out.get("operator") != leaf.get("operator") or out.get("value") != value:
            state.adjusted = True
        return out

    def _validate_child_age(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        value = leaf.get("value")
        text = str(leaf.get("source_text") or "")

        # 출생체중/재태기간 등 임상 수치는 나이가 아니므로 child_age로 저장하지 않는다.
        if self._looks_like_clinical_measure(value, text):
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="clinical_measure", reason=REASON_INVALID_FIELD_VALUE
                )
            )
            state.removed = True
            return None

        if self._is_effectively_empty(value):
            # [None], null 등은 그냥 제거한다.
            state.removed = True
            return None

        coerced, ok = self._coerce_child_age_value(value)
        if not ok:
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="child_age", reason=REASON_INVALID_FIELD_VALUE
                )
            )
            state.removed = True
            return None

        out = dict(leaf)
        out["value"] = coerced
        if coerced != value:
            state.adjusted = True
        return out

    def _validate_income_status(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        value = leaf.get("value")
        text = str(leaf.get("source_text") or "")

        percent = self._extract_median_percent(value, text)
        is_percent_value = (
            isinstance(value, dict)
            and any(k in value for k in ("percent", "median_income_percent"))
        ) or ("중위소득" in text and percent is not None)

        if is_percent_value:
            if percent is None:
                state.unknowns.append(
                    {
                        "field": "median_income_percent",
                        "value": value,
                        "source_text": leaf.get("source_text"),
                        "reason": REASON_INVALID_FIELD_VALUE,
                    }
                )
                state.removed = True
                return None
            out = dict(leaf)
            out["field"] = "median_income_percent"
            out["operator"] = self._percent_operator(text)
            out["value"] = {"percent": percent}
            out["matching_strength"] = STRENGTH_HARD
            state.adjusted = True
            return out

        # percent가 아닌 dict는 income_status로 저장할 수 없다.
        if isinstance(value, dict):
            state.unknowns.append(
                {
                    "field": "income_status",
                    "value": value,
                    "source_text": leaf.get("source_text"),
                    "reason": REASON_INVALID_FIELD_VALUE,
                }
            )
            state.removed = True
            return None

        return leaf

    def _validate_median_income(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        value = leaf.get("value")
        text = str(leaf.get("source_text") or "")
        percent = self._extract_median_percent(value, text)
        if percent is None:
            state.unknowns.append(
                {
                    "field": "median_income_percent",
                    "value": value,
                    "source_text": leaf.get("source_text"),
                    "reason": REASON_INVALID_FIELD_VALUE,
                }
            )
            state.removed = True
            return None
        out = dict(leaf)
        out["operator"] = self._percent_operator(text, default=leaf.get("operator"))
        out["value"] = {"percent": percent}
        if out["value"] != value or out.get("operator") != leaf.get("operator"):
            state.adjusted = True
        return out

    def _validate_special_condition(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        value = leaf.get("value")
        text = str(leaf.get("source_text") or "")

        # 원문이 농업인/학대/실직 등 special_condition이 될 수 없는 내용이면,
        # enum 값이 형식상 유효해도(억지 매핑) hard에서 분리한다.
        if any(keyword in text for keyword in _SPECIAL_CONDITION_MISMATCH_KEYWORDS):
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="special_condition", reason=REASON_FIELD_NOT_SUPPORTED
                )
            )
            state.removed = True
            return None

        tokens = self._as_token_list(value)
        normalized_tokens = [str(t).strip().lower() for t in tokens if t is not None]
        valid = [t for t in normalized_tokens if t in _SPECIAL_CONDITION_ALLOWED_VALUES]
        invalid = [t for t in normalized_tokens if t not in _SPECIAL_CONDITION_ALLOWED_VALUES]

        if invalid:
            # 표준 enum에 없는 값은 hard로 저장하지 않는다.
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="special_condition", reason=REASON_FIELD_NOT_SUPPORTED
                )
            )
            state.removed = True

        if not valid:
            return None

        out = dict(leaf)
        if len(valid) == 1:
            out["value"] = valid[0]
        else:
            out["operator"] = "IN"
            out["value"] = valid
        if out.get("value") != value or out.get("operator") != leaf.get("operator"):
            state.adjusted = True
        return out

    def _validate_region(
        self,
        leaf: dict[str, Any],
        state: _ValidationState,
    ) -> dict[str, Any] | None:
        value = leaf.get("value")
        text = str(leaf.get("source_text") or "")

        # 자산/재산 기준 금액이 region에 들어간 경우 분리한다.
        if self._looks_like_asset_criterion(value, text):
            state.unsupported.append(
                self._unsupported_item(
                    leaf, field="asset", reason=REASON_ASSET_NOT_REGION
                )
            )
            state.removed = True
            return None

        if self._is_effectively_empty(value):
            state.removed = True
            return None

        return leaf

    # --- validation 보조 헬퍼 ---

    def _as_token_list(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return []
        return [value]

    def _is_effectively_empty(self, value: Any) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, list):
            return all(
                v is None or (isinstance(v, str) and not v.strip()) for v in value
            )
        if isinstance(value, dict):
            return len(value) == 0
        return False

    def _coerce_scalar_int(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+", value)
            if match:
                return int(match.group())
        return None

    def _coerce_child_age_value(self, value: Any) -> tuple[Any, bool]:
        """child_age 값을 숫자/숫자배열/숫자dict로 강제한다. (값, 성공여부)."""
        if isinstance(value, bool):
            return None, False
        if isinstance(value, (int, float)):
            return int(value), True
        if isinstance(value, str):
            number = self._coerce_scalar_int(value)
            return (number, True) if number is not None else (None, False)
        if isinstance(value, list):
            numbers = [
                n
                for n in (self._coerce_scalar_int(v) for v in value)
                if n is not None
            ]
            return (numbers, True) if numbers else (None, False)
        if isinstance(value, dict):
            out: dict[str, int] = {}
            for key, raw in value.items():
                if str(key).strip().lower() in _CHILD_AGE_NUMERIC_KEYS:
                    number = self._coerce_scalar_int(raw)
                    if number is not None:
                        out[key] = number
            return (out, True) if out else (None, False)
        return None, False

    def _looks_like_clinical_measure(self, value: Any, text: str) -> bool:
        haystack = text.lower()
        if any(keyword in haystack for keyword in _CHILD_AGE_CLINICAL_KEYWORDS):
            return True
        if isinstance(value, dict):
            keys = {str(k).strip().lower() for k in value}
            if keys & {"grams", "gram", "weight", "kg", "weeks", "week", "gestation"}:
                return True
        return False

    def _looks_like_asset_criterion(self, value: Any, text: str) -> bool:
        if any(keyword in text for keyword in _REGION_ASSET_KEYWORDS):
            return True
        if _AMOUNT_PATTERN.search(text):
            return True
        if isinstance(value, str) and _AMOUNT_PATTERN.search(value):
            return True
        if isinstance(value, dict):
            keys = {str(k).strip().lower() for k in value}
            if keys & {"amount", "asset", "won", "krw", "money"}:
                return True
        return False

    def _extract_median_percent(self, value: Any, text: str) -> int | None:
        if isinstance(value, dict):
            for key in ("percent", "median_income_percent", "value"):
                number = self._coerce_scalar_int(value.get(key))
                if number is not None:
                    return number
        else:
            number = self._coerce_scalar_int(value)
            if number is not None:
                return number
        match = re.search(r"(\d+)\s*%", text)
        if match:
            return int(match.group(1))
        return None

    def _percent_operator(self, text: str, default: Any = None) -> str:
        if "이상" in text or "초과" in text:
            return "GT" if "초과" in text else "GTE"
        if "미만" in text:
            return "LT"
        if "이하" in text:
            return "LTE"
        normalized = self._normalize_operator(default) if default is not None else "UNKNOWN"
        if normalized in ("LT", "LTE", "GT", "GTE"):
            return normalized
        return "LTE"

    def _is_empty_tree(self, tree: Any) -> bool:
        if not isinstance(tree, dict) or not tree:
            return True
        conditions = tree.get("conditions")
        if isinstance(conditions, list):
            if not conditions:
                return True
            return all(self._is_empty_tree(child) for child in conditions)
        # leaf 노드(field 보유)는 비어있지 않다.
        return not bool(tree.get("field"))

    def _normalize_tree_node(
        self,
        node: Any,
        *,
        buckets: _Buckets,
    ) -> dict[str, Any] | None:
        if not isinstance(node, dict):
            return None

        conditions = node.get("conditions")
        if isinstance(conditions, list):
            operator = str(node.get("operator", "AND")).strip().upper()
            if operator not in ("AND", "OR"):
                operator = "AND"
            new_conditions: list[dict[str, Any]] = []
            for child in conditions:
                normalized = self._normalize_tree_node(child, buckets=buckets)
                if normalized is not None:
                    new_conditions.append(normalized)
            new_conditions = self._dedupe_conditions(new_conditions)
            if not new_conditions:
                return None
            result = dict(node)
            result["operator"] = operator
            result["conditions"] = new_conditions
            return result

        return self._normalize_leaf(node, buckets=buckets)

    def _dedupe_conditions(
        self,
        conditions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """같은 그룹 안에서 의미가 같은 leaf 중복을 제거한다(그룹 노드는 보존)."""
        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for cond in conditions:
            if isinstance(cond.get("conditions"), list):
                deduped.append(cond)
                continue
            value = cond.get("value")
            if isinstance(value, list):
                value_key = json.dumps(sorted(map(str, value)), ensure_ascii=False)
            else:
                value_key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            key = f"{cond.get('field')}|{cond.get('operator')}|{value_key}"
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cond)
        return deduped

    def _normalize_leaf(
        self,
        node: dict[str, Any],
        *,
        buckets: _Buckets,
    ) -> dict[str, Any] | None:
        leaf = dict(node)
        raw_operator = leaf.get("operator")

        # EXCLUDES는 operator가 아니라 exclusions 구조로 분리한다.
        if raw_operator is not None and str(raw_operator).strip().upper() == "EXCLUDES":
            buckets.exclusions.append(
                {
                    "type": leaf.get("type", "exclusion"),
                    "field": self._normalize_field_name(leaf.get("field")),
                    "value": leaf.get("value"),
                    "source_text": leaf.get("source_text"),
                    "confidence": leaf.get("confidence"),
                }
            )
            return None

        # alias를 표준 field로 먼저 해석한다.
        mapping = self._field_normalization(leaf.get("field"))
        if mapping is not None:
            leaf["field"] = mapping["field"]
            if "operator" in mapping:
                leaf["operator"] = mapping["operator"]
            if "value" in mapping and not leaf.get("value"):
                leaf["value"] = mapping["value"]

        field_key = self._field_key(leaf.get("field"))

        # tier별 처리: hard/soft/follow_up은 tree에 남기고 matching_strength 부여.
        strength = self._matching_strength(field_key)
        if strength is not None:
            leaf["field"] = field_key
            leaf["operator"] = self._normalize_operator(leaf.get("operator"))
            leaf["matching_strength"] = strength
            if strength == STRENGTH_FOLLOW_UP:
                buckets.follow_up_required = True
            return leaf

        # 국적/체류/난민 등 식별 조건은 canonical field로 unsupported에 보존한다.
        identity_field = self._out_of_scope_field(field_key)
        if identity_field is not None:
            buckets.unsupported.append(
                self._unsupported_item(
                    leaf, field=identity_field, reason=REASON_FIELD_NOT_SUPPORTED
                )
            )
            return None

        # 상세 장애 유형/등급은 UI가 수집하지 않으므로 unsupported에 보존한다.
        if self._is_detailed_disability_field(field_key):
            buckets.unsupported.append(
                self._unsupported_item(
                    leaf, field=field_key, reason=REASON_DETAILED_DISABILITY
                )
            )
            return None

        # 농업인 등 그 외 미지원 조건도 unsupported에 보존한다.
        if self._is_unsupported_condition_field(field_key):
            buckets.unsupported.append(
                self._unsupported_item(
                    leaf, field=field_key, reason=REASON_FIELD_NOT_SUPPORTED
                )
            )
            return None

        # 그 외 표현 불가/미상(field unknown 등) 조건은 unknowns로 분리한다.
        buckets.unknowns.append(
            {
                "field": field_key,
                "value": leaf.get("value"),
                "source_text": leaf.get("source_text"),
                "reason": REASON_FIELD_NOT_SUPPORTED,
            }
        )
        return None

    def _matching_strength(self, field_key: str | None) -> str | None:
        if not field_key:
            return None
        if field_key in _HARD_FIELDS:
            return STRENGTH_HARD
        if field_key in _SOFT_FIELDS:
            return STRENGTH_SOFT
        if field_key in _FOLLOW_UP_FIELDS:
            return STRENGTH_FOLLOW_UP
        return None

    def _unsupported_item(
        self,
        leaf: dict[str, Any],
        *,
        field: Any,
        reason: str,
    ) -> dict[str, Any]:
        return {
            "type": "unsupported",
            "field": field,
            "value": leaf.get("value"),
            "source_text": leaf.get("source_text"),
            "reason": reason,
        }

    def _is_detailed_disability_field(self, field_key: str | None) -> bool:
        if not field_key:
            return False
        if field_key in _DETAILED_DISABILITY_FIELDS:
            return True
        return "disabilit" in field_key

    def _normalize_operator(self, operator: Any) -> str:
        if operator is None:
            return "UNKNOWN"
        return _OPERATOR_ALIASES.get(str(operator).strip().upper(), "UNKNOWN")

    def _field_key(self, field: Any) -> str | None:
        if not field:
            return None
        return str(field).strip().lower()

    def _out_of_scope_field(self, field_key: str | None) -> str | None:
        if not field_key:
            return None
        if field_key in _OUT_OF_SCOPE_FIELDS:
            return field_key
        for keyword, canonical in _OUT_OF_SCOPE_KEYWORDS.items():
            if keyword in field_key:
                return canonical
        return None

    def _is_unsupported_condition_field(self, field_key: str | None) -> bool:
        if not field_key:
            return False
        if field_key in _UNSUPPORTED_CONDITION_FIELDS:
            return True
        return any(keyword in field_key for keyword in _UNSUPPORTED_CONDITION_KEYWORDS)

    def _field_normalization(self, field: Any) -> dict[str, Any] | None:
        if not field:
            return None
        return _FIELD_NORMALIZATION.get(str(field).strip().lower())

    def _normalize_field_name(self, field: Any) -> Any:
        mapping = self._field_normalization(field)
        if mapping is not None:
            return mapping["field"]
        return field

    def _sanitize_exclusion(
        self,
        item: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """exclusions 항목에서 EXCLUDES operator를 제거하고 enum을 표준화한다."""
        out = dict(item)
        operator = out.get("operator")
        if operator is not None:
            if str(operator).strip().upper() == "EXCLUDES":
                out.pop("operator", None)
            else:
                out["operator"] = self._normalize_operator(operator)
        if reason is not None:
            out.setdefault("reason", reason)
        return out

    def _add_flag(self, quality_flags: list[Any], flag: str) -> None:
        if flag not in quality_flags:
            quality_flags.append(flag)

    def _exclusion_text(self, item: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("source_text", "value", "reason", "note", "description"):
            value = item.get(key)
            if value:
                parts.append(str(value))
        return " ".join(parts)

    def _is_calculation_exclusion(self, text: str) -> bool:
        content = text or ""
        return ("산출" in content or "산정" in content) and "제외" in content

    def _has_positive(self, text: str) -> bool:
        content = text or ""
        return any(k in content for k in _POSITIVE_PHRASES)

    def _has_explicit_exclusion(self, text: str) -> bool:
        content = text or ""
        return any(k in content for k in _EXCLUSION_NEGATIVE_KEYWORDS)

    def _classify_exclusion(self, text: str) -> str:
        """exclusion 문구를 'exclusion' / 'calculation' / 'not_exclusion'으로 분류."""
        if self._is_calculation_exclusion(text):
            return "calculation"
        if self._has_positive(text) and not self._has_explicit_exclusion(text):
            return "not_exclusion"
        return "exclusion"

    def _with_reason(self, item: dict[str, Any], reason: str) -> dict[str, Any]:
        note = dict(item)
        note.setdefault("reason", reason)
        return note

    def _system_prompt(self) -> str:
        return """
당신은 복지 정책 원문을 추천/지원 가능성 판정용 조건 JSON으로 구조화하는 백엔드 Agent입니다.
반드시 제공된 원문 안의 내용만 사용하세요. 원문에 없는 기준값을 추측하거나 보완하지 마세요.

목표:
- 정책 지원대상과 선정기준을 AND/OR 관계가 보존된 condition_tree로 정리합니다.
- 모든 조건에는 가능하면 type, field, operator, value, source_text, confidence를 포함합니다.
- 명시적으로 배제되는 조건만 exclusions에 분리합니다.
- 서비스가 수집하지 않거나 제외 조건이 아닌 참고 문구는 ignored_conditions에 둡니다.
- 정책 핵심 대상이 서비스 범위 밖이면 unsupported_conditions에 두고 review_required=true로 둡니다.
- 산정·산출 방식 관련 문구는 special_notes에 둡니다.
- 원문이 모호하거나 기준값이 없으면 unknowns에 넣고 review_required를 true로 둡니다.

원문 우선순위:
- OpenAPI의 category, sub_category, lifeArray, trgterIndvdlArray는 참고 정보일 뿐입니다.
- 실제 조건 판단은 지원대상, 선정기준, target_description 원문을 우선합니다.
- OpenAPI 분류 정보와 지원대상/선정기준 원문이 충돌하면 지원대상/선정기준 원문을 기준으로 condition_tree를 작성합니다.

operator는 반드시 다음 enum 중 하나만 사용합니다(그 외 표현 금지):
- EXISTS: 특정 자격/상태를 보유
- EQ: 값이 같음
- IN: 값이 목록 중 하나에 포함
- LTE / GTE / LT / GT: 수치 비교(이하/이상/미만/초과)
- UNKNOWN: 기준값을 알 수 없음
주의: EXCLUDES는 operator가 아닙니다. 배제 조건은 condition_tree가 아니라 exclusions에 넣습니다.
EQUAL/EQUALS는 EQ로, exists는 EXISTS로, unknown은 UNKNOWN으로 통일합니다.
수치 경계는 정확히 구분합니다: "미만"=LT, "이하"=LTE, "초과"=GT, "이상"=GTE.
예: "2세 미만"은 child_age + LT 2 입니다(LTE 2 아님).

condition_tree에는 현재 추천 UI / Condition Agent가 실제로 수집·비교하는 표준 field만 넣습니다.
사용 가능한 표준 field(임의 생성 금지):
stage, child_age, income_status, median_income_percent, region, household_type,
special_condition, pregnancy_status, household_member_age, age
- stage 허용값은 정확히 다음 5개뿐입니다: pregnant, newborn, infant, child, youth.
  "임산부/임신중"=stage IN ["pregnant"], "출산직후/신생아"=stage IN ["newborn"],
  "영유아"=stage IN ["newborn","infant"], "아동"=stage IN ["child"],
  "청소년/청년"=stage IN ["youth"]. 같은 의미를 중복으로 두 번 넣지 마세요.
  주의: 출산/유산/사산(birth/miscarriage/stillbirth), 나이 숫자, null은 stage가 아닙니다.
  stage에 넣지 말고, 출산/유산/사산 같은 이벤트는 unsupported_conditions로 보냅니다.
  근거가 없으면 stage 조건을 만들지 말고(특히 operator EXISTS에 value null 금지) 생략합니다.
- child_age는 숫자 또는 숫자 배열만 value로 씁니다("만 나이" 기준). 예: "만 6세 미만"=
  child_age + LT 6, "3~5세"=child_age IN [3,4,5]. "3세" 같은 문자열·null·[null]은 쓰지 마세요.
  출생체중(2500g)·재태기간(37주) 같은 임상 수치는 child_age가 아닙니다 →
  unsupported_conditions로 보냅니다. 나이 구간이 없으면 child_age 조건을 만들지 마세요.
- special_condition 허용 enum은 정확히 다음 5개뿐입니다: single_parent_or_grandparent(한부모/조손),
  multicultural_or_defector(다문화/탈북민), disabled_household(장애인 가구),
  multichild(다자녀), low_income(저소득 가구).
  위기사유/학대피해/성폭력피해/중한 질병/실직/긴급돌봄 필요/농업인 등은 이 enum에 억지로
  매핑하지 마세요. 해당하는 표준 field가 없으면 unsupported_conditions 또는 unknowns로 보냅니다.
- income_status는 "자격 상태"만 담습니다: basic_livelihood_recipient(생계급여/기초생활수급),
  near_poverty_class(차상위계층). "수급자 또는 차상위계층"=income_status IN
  ["basic_livelihood_recipient","near_poverty_class"]. income_status value에 percent 숫자/
  dict(예: {"percent":150})를 넣지 마세요.
- 기준중위소득 비율은 median_income_percent에 {"percent": n} 형태로 담습니다.
  "가구 기준중위소득 250% 이하"=median_income_percent + LTE {"percent": 250}.
- region에는 지역 코드/지역명만 담습니다. 대도시/중소도시/농어촌 "자산·재산 기준 금액"은
  region이 아닙니다 → 해당 조건은 unsupported_conditions로 보냅니다.
- "34세 이하"는 household_member_age + LTE 34로 표현합니다.

조건은 매칭 강도(matching_strength)를 3단계로 구분해 각 leaf에 표시합니다.

[hard] 선택식 UI에서 직접 받는 확정 매칭 조건 → condition_tree에 넣고 matching_strength="hard".
  field: stage, child_age, income_status, median_income_percent, region, household_type,
  special_condition, pregnancy_status, household_member_age, age

[soft] 선택식 UI에는 없지만 자연어에서 추출 가능, 추천 점수/후보 보정에 활용(확정 아님)
  → condition_tree에 넣고 matching_strength="soft".
  field: employment_status(근로자/재직자/고용보험), worker_status, employee_status,
  insurance_status(건강보험 가입/피부양), program_participation, benefit_overlap

[follow_up] 판단에 중요하지만 사용자 입력에 없으면 추가 확인이 필요한 조건
  → condition_tree에 넣고 matching_strength="follow_up", review_required=true로 둡니다.
  field: accident_victim_status(자동차사고 피해), debt_status/bankruptcy_status(개인회생/파산),
  legal_consultation_need(법률상담 필요), environmental_damage_type(환경오염 피해)

soft/follow_up 조건은 무조건 버리지 마세요. 자연어 입력에서 중요할 수 있으므로 tree에 남깁니다.
다만 hard 조건처럼 확정 매칭으로 과신하지 말고 강도를 정확히 표시합니다.

[unsupported] 현재 서비스에서 자연어로도 안정 처리 어렵거나 추천 범위와 거리가 먼 조건
  → condition_tree에 넣지 말고 unsupported_conditions로 분리, review_required=true.
  - 국적/체류/난민/귀화/비자: nationality_status, residency_status, refugee_status,
    naturalization_status, visa_status, legal_stay_status
  - 상세 장애: 상세 장애 유형/등급(detailed_disability_type, disability_grade, 발달장애 등).
    단 "장애인 가구" 수준은 special_condition=disabled_household(hard)로 살릴 수 있습니다.
  - 직업: 농업인(farmer_status) 등
표현 불가/미상 조건이나 field "unknown"은 condition_tree에 넣지 말고 unknowns로 분리합니다.
자연어 대비를 이유로 임의의 새 field를 만들지 마세요. 위 목록에 없는 field가 필요하면
unknowns로 보내고 review_required=true로 둡니다.

OpenAPI 분류 metadata 과신 금지(아주 중요):
- category, sub_category, lifeArray, trgterIndvdlArray의 "저소득", "청년" 같은 값은 참고용입니다.
- 지원대상/선정기준 원문에 명확한 대상 조건으로 등장하지 않으면 active condition_tree에 넣지 마세요.
  예: 지원대상이 "법률 상담이 필요한 국민"인데 분류값에 "저소득/청년"이 있어도
  income_status나 stage 조건을 만들지 않습니다.

exclusions 판별 규칙(아주 중요):
- "지원 가능", "포함 가능", "대상에 해당", "신청 가능"은 제외가 아닙니다. exclusions에 넣지 마세요.
  예: "외국인과 난민도 자격 기준에 맞으면 지원 가능"은 exclusions가 아니라 ignored_conditions로 둡니다.
- "지원 제외", "대상 제외", "제외한다", "중복지원 불가", "지원하지 않음", "자격상실",
  "급여정지"처럼 명시적으로 배제되는 경우만 exclusions에 넣습니다.
- "가구원 수 산출에서 제외", "소득 산정에서 제외"는 대상 제외가 아니라 산정 방식 문구이므로
  exclusions가 아니라 special_notes에 두고 애매하면 review_required=true로 둡니다.

AND/OR 해석 규칙(아주 중요):
- "또는", "중 하나", "해당하는 자", 대상 유형 나열("임산부, 영유아, 아동, 청년")은 OR입니다.
- "수급자 또는 차상위계층"은 OR입니다. 절대 AND로 묶지 마세요.
- "중", "이면서", "모두", "그리고", "필수"는 AND입니다.
- "A 중 B" 형태는 보통 A가 선행 필수 조건이고 B가 세부 대상 조건입니다.
  따라서 root operator는 AND이고, A는 필수 leaf, B(여러 대상 유형 나열)는 OR 그룹입니다.
  예: "생계급여 수급가구 중 임산부, 영유아, 아동, 청년이 포함된 가구"는
  root AND = 생계급여 수급가구(필수) AND (임산부 OR 영유아 OR 아동 OR 청년).
  이때 root를 OR로 만들면 "수급가구이거나 임산부면 가능"이 되어 의미가 틀립니다.
- 공통으로 걸리는 소득/자격 조건은 특정 대상 그룹 안에만 넣지 말고 상위 AND 레벨에 둡니다.
  예: "사고 피해 대상자 중 수급자 또는 차상위계층"은
  root AND = (수급자 OR 차상위계층) AND (피해 대상 유형 OR 그룹).
- "발달장애인 재직자"처럼 서로 다른 축의 조건이 함께 충족되어야 하면 AND입니다.
  이때 발달장애는 special_condition=disabled_household(hard)로, 근로자/재직자는
  employment_status=employed(soft)로 표현합니다.
- 정책명에 핵심 자격 조건이 들어 있으면(예: "발달장애인 재직자 훈련") 지원대상 원문과 함께
  참고하여 핵심 조건을 누락하지 마세요.

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
          "field": "median_income_percent",
          "operator": "LTE",
          "value": {"percent": 32},
          "source_text": "기준중위소득 32% 이하",
          "matching_strength": "hard",
          "confidence": 0.95
        }
      ]
    }
  ]
}

기타 주의:
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

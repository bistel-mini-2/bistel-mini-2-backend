import asyncio
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.common.policy_types import ChildAge, IncomeLevel, LifeStage, RegionCode
from app.schemas.ai_contract import (
    ConditionInput,
    ConditionResult,
    FollowUpCandidate,
    InputIssue,
)
from app.services.profile_condition_merge_service import ProfileConditionMergeService


logger = logging.getLogger(__name__)

ALLOWED_LIFE_STAGES = {item.value for item in LifeStage}
ALLOWED_CHILD_AGES = {item.value for item in ChildAge}
ALLOWED_INCOME_LEVELS = {item.value for item in IncomeLevel}
ALLOWED_REGION_CODES = {item.value for item in RegionCode}
ALLOWED_SPECIAL_FLAGS = {
    "single",
    "multi",
    "disabled",
    "many",
    "dual",
    "low_income",
    "veteran",
}

STAGE_ALIASES = {
    "임신": "pregnant",
    "임신중": "pregnant",
    "출산예정": "pregnant",
    "태아": "pregnant",
    "신생아": "newborn",
    "영아": "infant",
    "영유아": "infant",
    "아동": "child",
    "청소년": "teen",
}
REGION_ALIASES = {
    "전국": "national",
    "서울": "seoul",
    "부산": "busan",
    "대구": "daegu",
    "인천": "incheon",
    "광주": "gwangju",
    "대전": "daejeon",
    "울산": "ulsan",
    "세종": "sejong",
    "경기": "gyeonggi",
    "경기도": "gyeonggi",
    "강원": "gangwon",
    "충북": "chungbuk",
    "충남": "chungnam",
    "전북": "jeonbuk",
    "전남": "jeonnam",
    "경북": "gyeongbuk",
    "경남": "gyeongnam",
    "제주": "jeju",
}
SPECIAL_ALIASES = {
    "한부모": "single",
    "조손": "single",
    "다문화": "multi",
    "탈북": "multi",
    "장애": "disabled",
    "다자녀": "many",
    "맞벌이": "dual",
    "저소득": "low_income",
    "저소득층": "low_income",
    "기초생활": "low_income",
    "차상위": "low_income",
    "보훈": "veteran",
    # condition_profile / policy_rule enum이 입력으로 들어와도 표준값으로 정규화.
    "disabled_household": "disabled",
    "single_parent_or_grandparent": "single",
    "multichild": "many",
    "multicultural_or_defector": "multi",
}

# income_status는 EQ/IN exact 비교용 enum이므로, 한글/오타가 그대로 matcher까지 가면
# hard rule mismatch로 잘못 탈락한다. 허용값으로 정규화하고 모르는 값은 input issue로 남긴다.
ALLOWED_INCOME_STATUS = {
    "basic_livelihood_recipient",
    "livelihood_benefit_recipient",
    "medical_benefit_recipient",
    "housing_benefit_recipient",
    "education_benefit_recipient",
    "near_poverty_class",
    # 기초생활/차상위 등 수급 자격이 "없다"고 명시한 경우(부정). null과 구분한다.
    "none",
}
INCOME_STATUS_ALIASES = {
    "기초생활수급자": "basic_livelihood_recipient",
    "기초생활수급": "basic_livelihood_recipient",
    "기초수급자": "basic_livelihood_recipient",
    "수급자": "basic_livelihood_recipient",
    "생계급여": "livelihood_benefit_recipient",
    "생계급여수급자": "livelihood_benefit_recipient",
    "의료급여": "medical_benefit_recipient",
    "의료급여수급자": "medical_benefit_recipient",
    "주거급여": "housing_benefit_recipient",
    "주거급여수급자": "housing_benefit_recipient",
    "교육급여": "education_benefit_recipient",
    "교육급여수급자": "education_benefit_recipient",
    "차상위": "near_poverty_class",
    "차상위계층": "near_poverty_class",
    # 수급 자격 없음(부정) alias
    "없음": "none",
    "해당없음": "none",
    "해당없어요": "none",
    "비수급": "none",
    "수급아님": "none",
    "일반가구": "none",
}


class NaturalLanguageConditionExtraction(BaseModel):
    stage: str | None = None
    childAge: str | None = None
    income: str | None = None
    region: str | None = None
    special: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)
    income_status: str | list[str] | None = None
    age: int | None = None
    household_member_age: int | list[int] | None = None


class ConditionExtractor(Protocol):
    async def extract(self, raw_query: str) -> dict[str, Any]:
        ...


class LangChainConditionExtractor:
    def __init__(
        self,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 60,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def extract(self, raw_query: str) -> dict[str, Any]:
        if not raw_query.strip():
            return {}

        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(
            NaturalLanguageConditionExtraction)
        messages = [
            (
                "system",
                """
                    너는 한국 복지정책 추천 서비스의 조건 추출기다.

                    사용자의 한국어 자연어 입력에서 정책 추천에 필요한 조건을 추출해
                    NaturalLanguageConditionExtraction 스키마로만 반환한다.

                    반드시 아래 코드값만 사용한다.

                    stage:
                    - pregnant: 임신 중, 출산 예정, 태아
                    - newborn: 신생아
                    - infant: 영유아, 영아, 1~6세
                    - child: 아동, 초등학생, 7~12세
                    - teen: 청소년, 중고등학생, 13~18세

                    childAge:
                    - preborn: 태아, 출산 예정
                    - 0: 0세
                    - 1: 1세
                    - 2-5: 2~5세
                    - 6-12: 6~12세
                    - 13+: 13세 이상

                    income:
                    - low: 기준 중위소득 50% 이하
                    - mid1: 50~100%
                    - mid2: 100~150%
                    - high: 150% 초과
                    - unknown: 모르거나 입력하지 않음

                    region:
                    national, seoul, busan, daegu, incheon, gwangju, daejeon,
                    ulsan, sejong, gyeonggi, gangwon, chungbuk, chungnam,
                    jeonbuk, jeonnam, gyeongbuk, gyeongnam, jeju

                    special:
                    - single: 한부모, 조손
                    - multi: 다문화, 탈북
                    - disabled: 장애
                    - many: 다자녀
                    - dual: 맞벌이
                    - low_income: 저소득
                    - veteran: 보훈

                    income_status (수급 자격을 명확히 말한 경우에만 채운다):
                    - basic_livelihood_recipient: "기초생활수급자"라고만 하고 급여 종류가 불명확
                    - livelihood_benefit_recipient: 생계급여 수급
                    - medical_benefit_recipient: 의료급여 수급
                    - housing_benefit_recipient: 주거급여 수급
                    - education_benefit_recipient: 교육급여 수급
                    - near_poverty_class: 차상위계층
                    - none: 기초생활/차상위 등 수급 자격이 "없다"고 명시한 경우
                      (예: "수급 자격 없어요", "기초생활·차상위 아니에요", "해당 안 돼요").
                    급여 종류를 명확히 말하면 해당 세부값을, 단순히 "기초생활수급자"면
                    basic_livelihood_recipient를 쓴다. 수급 자격이 없다고 명시하면 "none"을 쓴다.
                    여러 개면 배열로, 수급 관련 언급이 전혀 없으면 null로 둔다.
                    ("없다"는 명시적 부정이므로 null이 아니라 "none"으로 구분한다.)

                    age: 신청자 본인 나이(정수). 예: "저는 70세" → 70. 없으면 null.

                    household_member_age: 가구원의 나이(정수) 또는 나이 목록(정수 배열).
                    예: "65세 부모님과 5살 아이가 있어요" → [65, 5]. 본인 나이는 age에 둔다. 없으면 null.

                    사용자가 명확히 말하지 않은 스칼라 필드는 null로, 리스트 필드(special, needs)는 빈 배열로 둔다.
                    사용자의 관심사나 원하는 지원 내용은 needs에 한국어 키워드로 담는다.
                    설명 문장을 추가하지 말고 스키마 필드만 채운다.
                    """
            ),
            ("user", raw_query),
        ]
        try:
            result = await asyncio.wait_for(
                structured_llm.ainvoke(messages),
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            # LLM 실패(타임아웃/쿼터 초과/네트워크 등) 시 빈 추출로 degrade한다.
            # 입력 파싱이 죽어 요청 전체가 실패하지 않도록, 폼 selected_conditions
            # 기반 룰 추천은 계속 진행되게 한다.
            logger.warning(
                "Condition extraction LLM failed, falling back to empty extraction: %s: %s",
                type(exc).__name__,
                exc,
            )
            return {}
        if isinstance(result, NaturalLanguageConditionExtraction):
            return result.model_dump(mode="json", exclude_none=True)
        if isinstance(result, dict):
            return result
        return {}


class ConditionAgent:
    def __init__(self, extractor: ConditionExtractor | None = None) -> None:
        self.extractor = extractor or LangChainConditionExtractor()

    async def analyze(self, condition_input: ConditionInput) -> ConditionResult:
        raw_extracted: dict[str, Any] = {}
        if condition_input.raw_query:
            raw_extracted = await self.extractor.extract(condition_input.raw_query)
        raw_extracted = self._plain_dict(raw_extracted)

        raw_normalized, raw_issues = self._normalize_conditions(raw_extracted)
        selected_normalized, selected_issues = self._normalize_conditions(
            condition_input.selected_conditions or {}
        )
        current_condition = {**raw_normalized, **selected_normalized}
        merged_condition, profile_conflicts = ProfileConditionMergeService.merge(
            current_condition,
            condition_input.profile_snapshot,
        )

        missing_issues = self._missing_issues(merged_condition)
        input_issues = [*raw_issues, *selected_issues, *missing_issues]
        follow_up_candidates = self._follow_up_candidates(input_issues)

        return ConditionResult(
            parsed_query_json={
                "raw_query": condition_input.raw_query,
                "raw_query_extracted": raw_extracted,
                "selected_conditions": condition_input.selected_conditions,
                "current_condition": current_condition,
            },
            merged_condition_json=merged_condition,
            input_issues=input_issues,
            profile_conflicts=profile_conflicts,
            follow_up_candidates=follow_up_candidates,
        )

    def _plain_dict(self, value: Any) -> dict[str, Any]:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, dict):
            return {
                str(key): (
                    item.model_dump(mode="json", exclude_none=True)
                    if isinstance(item, BaseModel)
                    else item
                )
                for key, item in value.items()
            }
        return {}

    def _normalize_conditions(
        self,
        source: dict[str, Any],
    ) -> tuple[dict[str, Any], list[InputIssue]]:
        normalized: dict[str, Any] = {}
        issues: list[InputIssue] = []

        self._copy_scalar(
            source,
            normalized,
            issues,
            source_keys=("stage", "life_stage", "target_stage"),
            target_key="stage",
            allowed_values=ALLOWED_LIFE_STAGES,
            aliases=STAGE_ALIASES,
        )
        self._copy_scalar(
            source,
            normalized,
            issues,
            source_keys=("childAge", "child_age", "child_age_range"),
            target_key="childAge",
            allowed_values=ALLOWED_CHILD_AGES,
        )
        self._copy_scalar(
            source,
            normalized,
            issues,
            source_keys=("income", "income_level"),
            target_key="income",
            allowed_values=ALLOWED_INCOME_LEVELS,
        )
        self._copy_scalar(
            source,
            normalized,
            issues,
            source_keys=("region", "region_code"),
            target_key="region",
            allowed_values=ALLOWED_REGION_CODES,
            aliases=REGION_ALIASES,
        )
        self._copy_special(source, normalized, issues)

        # income_status는 EQ/IN exact 비교 enum이라 검증/정규화 후 보존(모르는 값은 issue).
        self._copy_income_status(source, normalized, issues)
        # age/household_member_age는 수치라 비숫자면 matcher가 None(manual)로 안전 처리 → 무검증 보존.
        self._copy_raw(source, normalized, ("age", "user_age"), "age")
        self._copy_raw(
            source,
            normalized,
            ("household_member_age", "household_member_ages", "household_ages"),
            "household_member_age",
        )

        needs = source.get("needs") or source.get("user_needs")
        if isinstance(needs, list) and needs:
            normalized["needs"] = [str(item)
                                   for item in needs if item not in (None, "")]
        return normalized, issues

    def _copy_raw(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        source_keys: tuple[str, ...],
        target_key: str,
    ) -> None:
        """별칭 중 먼저 존재하는 값을 검증 없이 그대로 표준 키로 보존한다."""
        source_key = next((key for key in source_keys if key in source), None)
        if source_key is None:
            return
        value = source[source_key]
        if value in (None, "", []):
            return
        target[target_key] = value

    def _copy_income_status(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        issues: list[InputIssue],
    ) -> None:
        """income_status를 허용 enum으로 정규화한다. 모르는 값은 버리고 issue로 남긴다."""
        source_key = next(
            (key for key in ("income_status", "benefit_status") if key in source),
            None,
        )
        if source_key is None:
            return
        raw = source[source_key]
        if raw in (None, "", []):
            return
        items = raw if isinstance(raw, list) else [raw]

        valid: list[str] = []
        for item in items:
            if item in (None, ""):
                continue
            value = self._normalize_code(str(item), INCOME_STATUS_ALIASES)
            if value in ALLOWED_INCOME_STATUS:
                valid.append(value)
            else:
                issues.append(
                    InputIssue(
                        field_name="income_status",
                        issue_type="invalid",
                        message=f"Unsupported income_status: {item}",
                        priority=2,
                    )
                )
        if valid:
            deduped = list(dict.fromkeys(valid))
            target["income_status"] = deduped[0] if len(deduped) == 1 else deduped

    def _copy_scalar(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        issues: list[InputIssue],
        source_keys: tuple[str, ...],
        target_key: str,
        allowed_values: set[str],
        aliases: dict[str, str] | None = None,
    ) -> None:
        source_key = next((key for key in source_keys if key in source), None)
        if source_key is None or source[source_key] in (None, ""):
            return

        value = source[source_key]
        if isinstance(value, list):
            if len(value) == 1:
                value = value[0]
            else:
                issues.append(
                    InputIssue(
                        field_name=target_key,
                        issue_type="ambiguous",
                        message=f"Multiple values supplied for {target_key}",
                        priority=2,
                    )
                )
                return

        normalized_value = self._normalize_code(str(value), aliases or {})
        if normalized_value in allowed_values:
            target[target_key] = normalized_value
            return
        issues.append(
            InputIssue(
                field_name=target_key,
                issue_type="invalid",
                message=f"Unsupported value for {target_key}: {value}",
                priority=1,
            )
        )

    def _copy_special(
        self,
        source: dict[str, Any],
        target: dict[str, Any],
        issues: list[InputIssue],
    ) -> None:
        special: Any = []
        for key in ("special", "special_flags", "special_conditions", "special_condition"):
            candidate = source.get(key)
            if candidate not in (None, "", []):
                special = candidate
                break
        if special in (None, ""):
            special = []
        if isinstance(special, str):
            special = [special]
        if not isinstance(special, list):
            issues.append(
                InputIssue(
                    field_name="special",
                    issue_type="invalid",
                    message="special must be a list",
                    priority=2,
                )
            )
            return

        valid_special: list[str] = []
        for item in special:
            value = self._normalize_code(str(item), SPECIAL_ALIASES)
            if value in ALLOWED_SPECIAL_FLAGS:
                valid_special.append(value)
            else:
                issues.append(
                    InputIssue(
                        field_name="special",
                        issue_type="invalid",
                        message=f"Unsupported special condition: {item}",
                        priority=1,
                    )
                )
        if valid_special:
            target["special"] = list(dict.fromkeys(valid_special))

    def _missing_issues(self, merged_condition: dict[str, Any]) -> list[InputIssue]:
        issues: list[InputIssue] = []
        if not merged_condition.get("stage") and not merged_condition.get("childAge"):
            issues.append(
                InputIssue(
                    field_name="stage",
                    issue_type="missing",
                    message="life stage or child age is missing",
                    priority=3,
                )
            )
        if not merged_condition.get("income"):
            issues.append(
                InputIssue(
                    field_name="income",
                    issue_type="missing",
                    message="income is missing",
                    priority=3,
                )
            )
        return issues

    def _follow_up_candidates(
        self,
        input_issues: list[InputIssue],
    ) -> list[FollowUpCandidate]:
        question_by_field = {
            "region": "거주 지역을 알려주세요.",
            "stage": "임신/출산/양육 단계나 자녀 나이를 알려주세요.",
            "income": "대략적인 소득 구간을 알려주세요.",
            "special": "해당되는 가구 특성이 있는지 알려주세요.",
        }
        candidates = [
            FollowUpCandidate(
                field_name=issue.field_name,
                question_text=question_by_field.get(
                    issue.field_name,
                    "추천에 필요한 정보를 조금 더 알려주세요.",
                ),
                reason=issue.message,
                issue_type=issue.issue_type,
                message=issue.message,
                priority=issue.priority,
            )
            for issue in sorted(
                input_issues,
                key=lambda item: item.priority,
                reverse=True,
            )
            if issue.issue_type in {"missing", "ambiguous"} and issue.priority >= 3
        ]
        return candidates[:2]

    def _normalize_code(self, value: str, aliases: dict[str, str]) -> str:
        compact = value.strip().replace(" ", "")
        return aliases.get(compact, value.strip())

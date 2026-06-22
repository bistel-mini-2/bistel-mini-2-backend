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


ALLOWED_LIFE_STAGES = {item.value for item in LifeStage}
ALLOWED_CHILD_AGES = {item.value for item in ChildAge}
ALLOWED_INCOME_LEVELS = {item.value for item in IncomeLevel}
ALLOWED_REGION_CODES = {item.value for item in RegionCode}
ALLOWED_SPECIAL_FLAGS = {"single", "multi", "disabled", "many", "dual", "veteran"}

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
    "보훈": "veteran",
}


class NaturalLanguageConditionExtraction(BaseModel):
    stage: str | None = None
    childAge: str | None = None
    income: str | None = None
    region: str | None = None
    special: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)


class ConditionExtractor(Protocol):
    async def extract(self, raw_query: str) -> dict[str, Any]:
        ...


class LangChainConditionExtractor:
    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model

    async def extract(self, raw_query: str) -> dict[str, Any]:
        if not raw_query.strip():
            return {}

        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=self.model, temperature=0)
        structured_llm = llm.with_structured_output(NaturalLanguageConditionExtraction)
        result = await structured_llm.ainvoke(
            [
                (
                    "system",
                    "Extract Korean welfare recommendation conditions into the "
                    "given schema. Use only these codes when possible: "
                    "stage=pregnant|newborn|infant|child|teen, "
                    "childAge=preborn|0|1|2-5|6-12|13+, "
                    "income=low|mid1|mid2|high|unknown, "
                    "region=national|seoul|busan|daegu|incheon|gwangju|daejeon|"
                    "ulsan|sejong|gyeonggi|gangwon|chungbuk|chungnam|jeonbuk|"
                    "jeonnam|gyeongbuk|gyeongnam|jeju, "
                    "special=single|multi|disabled|many|dual|veteran.",
                ),
                ("user", raw_query),
            ]
        )
        if isinstance(result, NaturalLanguageConditionExtraction):
            return result.model_dump(exclude_none=True)
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

        needs = source.get("needs") or source.get("user_needs")
        if isinstance(needs, list) and needs:
            normalized["needs"] = [str(item) for item in needs if item not in (None, "")]
        return normalized, issues

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
        special = source.get("special", source.get("special_flags", []))
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
        if not merged_condition.get("region"):
            issues.append(
                InputIssue(
                    field_name="region",
                    issue_type="missing",
                    message="region is missing",
                    priority=3,
                )
            )
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
                    priority=2,
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
                reason="핵심 조건이 바뀌면 추천 후보가 달라질 수 있습니다.",
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

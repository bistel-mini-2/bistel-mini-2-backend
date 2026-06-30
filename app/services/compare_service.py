import logging
from typing import Annotated, Any

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.comparison_guide_agent import ComparisonGuideAgent
from app.common.exceptions import AppException, ErrorCode
from app.repositories.compare_repository import CompareRepository
from app.schemas.compare_schema import (
    CompareDiffItem,
    CompareHistoryItem,
    ComparePolicySummary,
    CompareRelatedPolicy,
    PolicyCompareResponse,
)


logger = logging.getLogger(__name__)


class CompareService:
    DIFF_FIELDS = (
        ("지원 대상 요약", "condition_profile_target_summary"),
        ("조건 원문", "condition_profile_source_text"),
        ("소득 조건", "income_conditions"),
        ("대상/연령 조건", "target_conditions"),
        ("제외/주의 조건", "caution_conditions"),
        ("추가 확인 필요", "condition_profile_review_required"),
        ("조건 신뢰도", "condition_profile_confidence"),
        ("제출 서류", "required_documents"),
    )

    def __init__(self) -> None:
        self.guide_agent = ComparisonGuideAgent()

    async def compare_policies(
        self,
        db: AsyncSession,
        *,
        slug_a: str,
        slug_b: str,
        user_id: int | None = None,
    ) -> PolicyCompareResponse:
        normalized_a = self._normalize_slug(slug_a)
        normalized_b = self._normalize_slug(slug_b)
        rows = await CompareRepository.find_policies_by_slugs(
            db,
            [normalized_a, normalized_b],
        )
        row_by_slug = {str(row["slug"]): row for row in rows}
        missing_slugs = [
            slug for slug in (normalized_a, normalized_b) if slug not in row_by_slug
        ]
        if missing_slugs:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message=f"Policy not found: {', '.join(missing_slugs)}",
            )

        policy_a = row_by_slug[normalized_a]
        policy_b = row_by_slug[normalized_b]
        related = await CompareRepository.find_related_policies(
            db,
            excluded_policy_ids=[
                int(policy_a["policy_id"]),
                int(policy_b["policy_id"]),
            ],
            category=policy_a.get("category") or policy_b.get("category"),
            tags=self._combined_tags(policy_a, policy_b),
        )
        await self._release_db_connection_before_llm(db)

        diff_table = self._diff_table(policy_a, policy_b)
        fallback_guide = self._selection_guide(policy_a, policy_b)
        selection_guide = await self.guide_agent.rewrite_selection_guide(
            policy_a=policy_a,
            policy_b=policy_b,
            diff_table=diff_table,
            fallback_guide=fallback_guide,
        )

        if user_id is not None:
            await CompareRepository.save_compare_history(
                db,
                user_id=user_id,
                policy_a_id=int(policy_a["policy_id"]),
                policy_b_id=int(policy_b["policy_id"]),
                policy_a_slug=str(policy_a["slug"]),
                policy_b_slug=str(policy_b["slug"]),
                policy_a_name=str(policy_a["name"]),
                policy_b_name=str(policy_b["name"]),
                selection_guide=selection_guide,
            )

        return PolicyCompareResponse(
            policy_a=self._to_policy_summary(policy_a),
            policy_b=self._to_policy_summary(policy_b),
            diff_table=diff_table,
            selection_guide=selection_guide,
            related_policies=[
                CompareRelatedPolicy(
                    policy_id=str(row["policy_id"]),
                    slug=str(row["slug"]),
                    name=str(row["name"]),
                )
                for row in related
            ],
        )

    async def get_compare_history(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        page: int,
        size: int,
    ) -> tuple[list[CompareHistoryItem], int]:
        rows, total = await CompareRepository.find_compare_history(
            db,
            user_id=user_id,
            page=page,
            size=size,
        )
        return [
            CompareHistoryItem(
                id=str(row["id"]),
                policy_a_name=str(row["policy_a_name"]),
                policy_b_name=str(row["policy_b_name"]),
                policy_a_slug=str(row["policy_a_slug"]),
                policy_b_slug=str(row["policy_b_slug"]),
                selection_guide=row.get("selection_guide"),
                compared_at=row["compared_at"],
            )
            for row in rows
        ], total

    async def delete_compare_history(
        self,
        db: AsyncSession,
        *,
        user_id: int,
        history_id: int,
    ) -> int:
        deleted = await CompareRepository.soft_delete_compare_history(
            db,
            user_id=user_id,
            history_id=history_id,
        )
        if not deleted:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Compare history not found",
            )
        return 1

    async def delete_all_compare_history(
        self,
        db: AsyncSession,
        *,
        user_id: int,
    ) -> int:
        return await CompareRepository.soft_delete_all_compare_history(
            db,
            user_id=user_id,
        )

    @staticmethod
    def _normalize_slug(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise AppException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code=ErrorCode.VALIDATION_ERROR,
                message="Policy slug is required",
            )
        return normalized

    @staticmethod
    async def _release_db_connection_before_llm(db: AsyncSession) -> None:
        commit = getattr(db, "commit", None)
        if commit is None:
            return
        try:
            await commit()
        except Exception:
            rollback = getattr(db, "rollback", None)
            if rollback is not None:
                await rollback()
            logger.exception("정책 비교 LLM 호출 전 DB 세션 정리 실패")
            raise

    @classmethod
    def _to_policy_summary(cls, row: dict[str, Any]) -> ComparePolicySummary:
        return ComparePolicySummary(
            policy_id=str(row["policy_id"]),
            slug=str(row["slug"]),
            name=str(row["name"]),
            summary={
                "benefit": cls._display_value(row.get("benefit_type")),
                "condition": cls._display_value(
                    row.get("condition_profile_target_summary")
                ),
                "source": cls._display_value(
                    row.get("condition_profile_source_text")
                ),
            },
        )

    @classmethod
    def _diff_table(
        cls,
        policy_a: dict[str, Any],
        policy_b: dict[str, Any],
    ) -> list[CompareDiffItem]:
        return [
            CompareDiffItem(
                field=label,
                a=cls._display_value(cls._field_value(policy_a, key)),
                b=cls._display_value(cls._field_value(policy_b, key)),
            )
            for label, key in cls.DIFF_FIELDS
        ]

    @classmethod
    def _selection_guide(
        cls,
        policy_a: dict[str, Any],
        policy_b: dict[str, Any],
    ) -> str:
        target_a = cls._display_value(
            policy_a.get("condition_profile_target_summary")
        )
        target_b = cls._display_value(
            policy_b.get("condition_profile_target_summary")
        )
        source_a = cls._display_value(policy_a.get("condition_profile_source_text"))
        source_b = cls._display_value(policy_b.get("condition_profile_source_text"))
        review_a = bool(policy_a.get("condition_profile_review_required"))
        review_b = bool(policy_b.get("condition_profile_review_required"))

        if not (target_a or source_a or target_b or source_b):
            return (
                f"{policy_a.get('name') or '첫 번째 정책'}은 공식 안내에서 핵심 혜택을 먼저 확인해 볼 만하고, "
                f"{policy_b.get('name') or '두 번째 정책'}은 함께 비교할 대안으로 볼 수 있습니다. "
                "아직 정리된 조건 정보가 부족하므로 두 정책의 혜택과 신청 준비 부담을 함께 확인해 주세요."
            )
        if target_a != target_b:
            return (
                f"{policy_a.get('name') or '첫 번째 정책'}과 {policy_b.get('name') or '두 번째 정책'}은 "
                "지원하는 대상과 활용 상황이 서로 다릅니다. "
                "첫 번째 정책은 비교표에 보이는 혜택이 더 필요할 때 먼저 볼 만하고, "
                "두 번째 정책은 다른 돌봄·지원 상황을 함께 검토할 때 좋은 대안이 될 수 있습니다."
            )
        if source_a != source_b:
            return (
                f"{policy_a.get('name') or '첫 번째 정책'}과 {policy_b.get('name') or '두 번째 정책'}은 대상 요약은 비슷하지만 세부 기준에서 장점이 갈립니다. "
                "한쪽은 조건이 단순하거나 준비가 쉬울 수 있고, 다른 한쪽은 더 구체적인 상황까지 다룰 수 있습니다. "
                "비교표의 혜택, 제출 서류, 주의 조건을 함께 보며 지금 활용하기 좋은 정책을 먼저 확인해 보세요."
            )
        if review_a or review_b:
            return (
                "두 정책 모두 장점은 있지만 세부 조건 확인이 필요한 부분이 있습니다. "
                f"{policy_a.get('name') or '첫 번째 정책'}은 조건이 맞으면 활용할 수 있는 혜택을 먼저 볼 수 있고, "
                f"{policy_b.get('name') or '두 번째 정책'}은 함께 검토할 대안으로 가치가 있습니다. "
                "실제 신청 전에는 제외 조건과 추가 확인 항목을 공식 안내에서 확인해 주세요."
            )
        return (
            "두 정책은 핵심 조건이 비슷해 보이므로 혜택의 성격과 신청 준비 부담이 선택 기준이 됩니다. "
            f"{policy_a.get('name') or '첫 번째 정책'}은 비교표에 보이는 혜택이 더 필요할 때 먼저 볼 만하고, "
            f"{policy_b.get('name') or '두 번째 정책'}은 제출 서류나 세부 기준이 더 잘 맞을 때 좋은 대안이 될 수 있습니다."
        )

    @classmethod
    def _field_value(cls, row: dict[str, Any], key: str) -> Any:
        if key == "income_conditions":
            return cls._conditions_by_domains(
                row.get("condition_profile_json"),
                {
                    "benefit_status",
                    "income",
                    "income_bracket",
                    "income_level",
                    "income_status",
                    "median_income_percent",
                },
            )
        if key == "target_conditions":
            return cls._conditions_by_domains(
                row.get("condition_profile_json"),
                {
                    "age",
                    "caregiver_type",
                    "child_age",
                    "disability",
                    "eligible_household",
                    "employment_status",
                    "family_type",
                    "household",
                    "household_type",
                    "household_member_age",
                    "life_stage",
                    "pregnancy_status",
                    "pregnancy_or_birth",
                    "special_condition",
                    "stage",
                    "target",
                    "target_context",
                    "target_stage",
                    "target_type",
                },
            )
        if key == "caution_conditions":
            return cls._caution_conditions(row.get("condition_profile_json"))
        if key == "condition_profile_review_required":
            return "추가 확인 필요" if row.get(key) else "추가 확인 항목 없음"
        if key == "condition_profile_confidence":
            value = row.get(key)
            if value is None:
                return None
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return value
        return row.get(key)

    @classmethod
    def _display_value(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            return ", ".join(
                str(item) for item in cls._unique_values(value)
                if item not in (None, "")
            )
        normalized = str(value).strip()
        return normalized or None

    @classmethod
    def _conditions_by_domains(
        cls,
        condition_json: Any,
        fields: set[str],
    ) -> list[str]:
        leaves = cls._condition_leaves(condition_json)
        return cls._unique_values([
            text
            for leaf in leaves
            if (
                str(leaf.get("field") or "") in fields
                or str(leaf.get("type") or "") in fields
            )
            for text in [cls._condition_text(leaf)]
            if text
        ])

    @classmethod
    def _caution_conditions(cls, condition_json: Any) -> list[str]:
        if not isinstance(condition_json, dict):
            return []
        values: list[str] = []
        for key in (
            "exclusions",
            "special_notes",
            "unknowns",
            "unsupported_conditions",
        ):
            for item in condition_json.get(key) or []:
                if isinstance(item, dict):
                    raw_text = (
                        item.get("source_text")
                        or item.get("text")
                        or item.get("reason")
                    )
                else:
                    raw_text = item
                text = cls._display_value(raw_text)
                if text:
                    values.append(text)
        return cls._unique_values(values)

    @classmethod
    def _condition_leaves(cls, condition_json: Any) -> list[dict[str, Any]]:
        if not isinstance(condition_json, dict):
            return []
        leaves: list[dict[str, Any]] = []

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            children = node.get("conditions")
            if isinstance(children, list) and children:
                for child in children:
                    walk(child)
                return
            if node.get("field") or node.get("type") or node.get("source_text"):
                leaves.append(node)

        walk(condition_json.get("condition_tree"))
        return leaves

    @classmethod
    def _condition_text(cls, leaf: dict[str, Any]) -> str | None:
        source_text = cls._display_value(leaf.get("source_text"))
        if source_text:
            return source_text
        field = cls._display_value(leaf.get("field") or leaf.get("type"))
        operator = cls._display_value(leaf.get("operator"))
        value = cls._display_value(leaf.get("value"))
        parts = [part for part in (field, operator, value) if part]
        return " ".join(parts) if parts else None

    @staticmethod
    def _unique_values(values: list[Any]) -> list[Any]:
        unique: list[Any] = []
        seen: set[str] = set()
        for value in values:
            key = str(value).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(value)
        return unique


    @staticmethod
    def _combined_tags(
        policy_a: dict[str, Any],
        policy_b: dict[str, Any],
    ) -> list[str]:
        tags: list[str] = []
        seen: set[str] = set()
        for row in (policy_a, policy_b):
            for tag in row.get("tags") or []:
                key = str(tag).casefold()
                if key in seen:
                    continue
                seen.add(key)
                tags.append(str(tag))
        return tags


CompareServiceDep = Annotated[CompareService, Depends(CompareService)]

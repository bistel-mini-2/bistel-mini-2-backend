from typing import Annotated, Any

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.exceptions import AppException, ErrorCode
from app.repositories.compare_repository import CompareRepository
from app.schemas.compare_schema import (
    CompareDiffItem,
    CompareHistoryItem,
    ComparePolicySummary,
    CompareRelatedPolicy,
    PolicyCompareResponse,
)


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
        if user_id is not None:
            await CompareRepository.save_compare_history(
                db,
                user_id=user_id,
                policy_a_id=int(policy_a["policy_id"]),
                policy_b_id=int(policy_b["policy_id"]),
            )

        return PolicyCompareResponse(
            policy_a=self._to_policy_summary(policy_a),
            policy_b=self._to_policy_summary(policy_b),
            diff_table=self._diff_table(policy_a, policy_b),
            selection_guide=self._selection_guide(policy_a, policy_b),
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

    @classmethod
    def _to_policy_summary(cls, row: dict[str, Any]) -> ComparePolicySummary:
        return ComparePolicySummary(
            policy_id=str(row["policy_id"]),
            slug=str(row["slug"]),
            name=str(row["name"]),
            summary={
                "benefit": cls._display_value(
                    row.get("condition_profile_source_text")
                ),
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
        name_a = str(policy_a["name"])
        name_b = str(policy_b["name"])
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

        if target_a != target_b:
            return (
                "두 정책은 지원 대상 조건이 다릅니다. "
                "각 정책의 조건 원문과 소득·연령·가구 조건을 먼저 비교한 뒤 "
                "본인 상황에 더 가까운 정책을 선택하세요."
            )
        if source_a != source_b:
            return (
                "지원 대상 요약은 비슷하지만 세부 조건 원문이 다릅니다. "
                "제외 조건과 추가 확인 항목을 함께 확인하세요."
            )
        if review_a or review_b:
            return (
                "두 정책 모두 조건 확인이 필요할 수 있습니다. "
                "수동 검토가 필요한 조건과 원문 근거를 먼저 확인하세요."
            )
        return (
            "두 정책의 핵심 조건이 비슷합니다. 조건 원문과 제출 서류를 함께 "
            "확인해 준비 부담이 적은 정책부터 진행하세요."
        )

    @classmethod
    def _field_value(cls, row: dict[str, Any], key: str) -> Any:
        if key == "income_conditions":
            return cls._conditions_by_domains(
                row.get("condition_profile_json"),
                {"income", "income_status", "median_income_percent"},
            )
        if key == "target_conditions":
            return cls._conditions_by_domains(
                row.get("condition_profile_json"),
                {
                    "age",
                    "child_age",
                    "household_member_age",
                    "life_stage",
                    "pregnancy_status",
                    "stage",
                    "target_stage",
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
            if str(leaf.get("field") or leaf.get("type") or "") in fields
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

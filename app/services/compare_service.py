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
        ("지원 금액", "benefit_description"),
        ("지원 대상", "target_description"),
        ("신청 기간", "application_period_text"),
        ("신청 방법", "application_method"),
        ("제출 서류", "required_documents"),
        ("지원 유형", "benefit_type"),
        ("담당 기관", "agency"),
        ("지역", "region"),
        ("문의처", "contact"),
        ("유의 사항", "caution"),
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
                "benefit": cls._display_value(row.get("benefit_description")),
                "condition": cls._display_value(row.get("target_description")),
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
        target_a = cls._display_value(policy_a.get("target_description"))
        target_b = cls._display_value(policy_b.get("target_description"))
        benefit_a = cls._display_value(policy_a.get("benefit_description"))
        benefit_b = cls._display_value(policy_b.get("benefit_description"))

        if target_a != target_b:
            return (
                f"{name_a}와 {name_b}는 지원 대상 조건이 다릅니다. "
                "가족 상황이 각 정책의 지원 대상에 먼저 해당하는지 확인한 뒤 "
                "혜택 규모와 신청 방법을 비교해 선택하세요."
            )
        if benefit_a != benefit_b:
            return (
                "지원 대상 조건이 비슷하다면 실제 받을 수 있는 지원 내용과 "
                "신청 기간을 기준으로 더 유리한 정책을 선택하세요."
            )
        return (
            "두 정책의 핵심 조건이 비슷합니다. 신청 기간, 제출 서류, 담당 기관을 "
            "함께 확인해 준비 부담이 적은 정책부터 진행하세요."
        )

    @classmethod
    def _field_value(cls, row: dict[str, Any], key: str) -> Any:
        if key == "region":
            if row.get("region_scope") == "NATIONAL":
                return "전국"
            return row.get("region_code")
        return row.get(key)

    @classmethod
    def _display_value(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if item not in (None, ""))
        normalized = str(value).strip()
        return normalized or None

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

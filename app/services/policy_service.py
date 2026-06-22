from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.policy_types import LIFE_STAGE_TO_TAGS
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import PolicyListItemResponse, PolicySort


class PolicyService:
    async def get_policy_list(
        self,
        db: AsyncSession,
        *,
        query: str | None,
        category: str | None,
        tags: list[str] | None,
        region_code: str | None,
        stage: str | None,
        sort: PolicySort,
        page: int,
        size: int,
    ) -> tuple[list[PolicyListItemResponse], int]:
        normalized_query = self._normalize_optional_text(query)
        normalized_category = self._normalize_optional_text(category)
        normalized_tags = self._normalize_text_list(tags)
        normalized_region_code = self._normalize_optional_text(region_code)
        normalized_stage = self._normalize_optional_text(stage)
        stage_tags = list(LIFE_STAGE_TO_TAGS.get(normalized_stage or "", ()))

        rows, total = await PolicyRepository.find_policy_list(
            db,
            query=normalized_query,
            query_pattern=self._to_like_pattern(normalized_query),
            category=normalized_category,
            tags=normalized_tags,
            region_code=normalized_region_code,
            stage_tags=stage_tags,
            sort=sort,
            page=page,
            size=size,
        )
        return [self._to_response(row) for row in rows], total

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _normalize_text_list(cls, values: list[str] | None) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for raw_value in values or []:
            for part in raw_value.split(","):
                value = cls._normalize_optional_text(part)
                if value is None:
                    continue
                key = value.casefold()
                if key in seen:
                    continue
                seen.add(key)
                normalized.append(value)

        return normalized

    @staticmethod
    def _to_like_pattern(query: str | None) -> str | None:
        if query is None:
            return None
        escaped = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        return f"%{escaped}%"

    @staticmethod
    def _to_response(row: dict[str, Any]) -> PolicyListItemResponse:
        return PolicyListItemResponse(
            policy_id=str(row["policy_id"]),
            slug=row["slug"],
            name=row["name"],
            category=row["category"],
            sub_category=row["sub_category"],
            tags=list(row["tags"] or []),
            target_stage=PolicyService._to_target_stages(row["tags"] or []),
            summary=row["summary"],
            benefit_summary=row["benefit_summary"],
            agency=row["agency"],
            benefit_type=row["benefit_type"],
            application_status=row["application_status"],
            application_start_date=row["application_start_date"],
            application_end_date=row["application_end_date"],
            deadline=row["application_end_date"],
            application_period_text=row["application_period_text"],
            region_scope=row["region_scope"],
            region_code=row["region_code"],
            region=(
                "national"
                if row["region_scope"] == "NATIONAL"
                else row["region_code"]
            ),
            official_url=row["official_url"],
        )

    @staticmethod
    def _to_target_stages(tags: list[str]) -> list[str]:
        normalized_tags = {tag.casefold() for tag in tags}
        return [
            stage
            for stage, stage_tags in LIFE_STAGE_TO_TAGS.items()
            if any(tag.casefold() in normalized_tags for tag in stage_tags)
        ]


PolicyServiceDep = Annotated[PolicyService, Depends(PolicyService)]

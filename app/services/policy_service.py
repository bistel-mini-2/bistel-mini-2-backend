from typing import Annotated, Any

from fastapi import Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.policy_types import LIFE_STAGE_TO_TAGS
from app.common.exceptions import AppException, ErrorCode
from app.repositories.policy_repository import PolicyRepository
from app.schemas.policy_schema import (
    PolicyConditionProfileResponse,
    PolicyDetailResponse,
    PolicyListItemResponse,
    PolicySort,
)


class PolicyService:
    async def get_policy_detail(
        self,
        db: AsyncSession,
        *,
        policy_slug: str,
    ) -> PolicyDetailResponse:
        normalized_slug = self._normalize_optional_text(policy_slug)
        if normalized_slug is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message="Policy not found",
            )

        row = await PolicyRepository.find_policy_detail(
            db,
            policy_slug=normalized_slug,
        )
        if row is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message="Policy not found",
            )
        detail = self._to_detail_response(row)
        related_rows = await PolicyRepository.find_related_policies(
            db,
            excluded_policy_ids=[int(row["policy_id"])],
            category=row["category"],
            region_scope=row["region_scope"],
            region_code=row["region_code"],
            target_stages=detail.target_stage,
            tags=list(row["tags"] or []),
            limit=3,
        )
        return detail.model_copy(
            update={
                "related_policies": [
                    self._to_response(related_row)
                    for related_row in related_rows
                ],
            },
        )

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
            stage=normalized_stage,
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
            target_stage=PolicyService._to_target_stages(row),
            summary=row["summary"],
            benefit_summary=row["benefit_summary"],
            agency=row["agency"],
            benefit_type=row["benefit_type"],
            application_status=row["application_status"],
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
    def _to_detail_response(row: dict[str, Any]) -> PolicyDetailResponse:
        list_response = PolicyService._to_response(row)
        condition_profile = PolicyService._to_condition_profile(row)
        return PolicyDetailResponse(
            **list_response.model_dump(),
            contact=row["contact"],
            benefit=row["benefit_description"],
            conditions=PolicyService._conditions_from_profile(
                condition_profile,
            )
            or row["target_description"],
            how_to_apply=row["application_method"],
            easy_summary=row["easy_summary"],
            target_description=row["target_description"],
            benefit_description=row["benefit_description"],
            application_method=row["application_method"],
            caution=row["caution"],
            condition_profile=condition_profile,
        )

    @staticmethod
    def _conditions_from_profile(
        condition_profile: PolicyConditionProfileResponse | None,
    ) -> str | None:
        if condition_profile is None:
            return None
        return (
            condition_profile.target_summary
            or condition_profile.source_text
        )

    @staticmethod
    def _to_condition_profile(
        row: dict[str, Any],
    ) -> PolicyConditionProfileResponse | None:
        condition_json = row.get("condition_profile_json")
        if condition_json is None:
            return None

        return PolicyConditionProfileResponse(
            condition_json=dict(condition_json or {}),
            target_summary=row.get("condition_profile_target_summary"),
            confidence=PolicyService._to_float(
                row.get("condition_profile_confidence")
            ),
            review_required=bool(row.get("condition_profile_review_required")),
            quality_flags=list(row.get("condition_profile_quality_flags") or []),
            source_text=row.get("condition_profile_source_text"),
            source_fields=[
                str(field)
                for field in row.get("condition_profile_source_fields") or []
            ],
        )

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_target_stages(row: dict[str, Any]) -> list[str]:
        profile_stages = PolicyService._target_stages_from_condition_json(
            row.get("condition_profile_json")
        )
        if profile_stages:
            return profile_stages
        return PolicyService._target_stages_from_tags(row["tags"] or [])

    @staticmethod
    def _target_stages_from_tags(tags: list[str]) -> list[str]:
        normalized_tags = {tag.casefold() for tag in tags}
        return [
            stage
            for stage, stage_tags in LIFE_STAGE_TO_TAGS.items()
            if any(tag.casefold() in normalized_tags for tag in stage_tags)
        ]

    @staticmethod
    def _target_stages_from_condition_json(condition_json: Any) -> list[str]:
        if not isinstance(condition_json, dict):
            return []

        stages: list[str] = []
        seen: set[str] = set()

        def append_stage(value: Any) -> None:
            for item in PolicyService._iter_condition_values(value):
                stage = PolicyService._normalize_stage_value(item)
                if stage is None or stage in seen:
                    continue
                seen.add(stage)
                stages.append(stage)

        def walk(node: Any) -> None:
            if not isinstance(node, dict):
                return
            field = str(node.get("field") or "").strip()
            if field in {"stage", "target_stage", "life_stage"}:
                append_stage(node.get("value"))
            elif field == "pregnancy_status" and node.get("value") is True:
                append_stage("pregnant")

            for child in node.get("conditions") or []:
                walk(child)

        walk(condition_json.get("condition_tree"))
        return stages

    @staticmethod
    def _iter_condition_values(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("stage", "target_stage", "life_stage", "value"):
                if value.get(key) is not None:
                    return PolicyService._iter_condition_values(value[key])
            return []
        return [value]

    @staticmethod
    def _normalize_stage_value(value: Any) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        aliases = {
            "pregnancy": "pregnant",
            "pregnant_woman": "pregnant",
            "youth": "teen",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized in LIFE_STAGE_TO_TAGS:
            return normalized
        return None


PolicyServiceDep = Annotated[PolicyService, Depends(PolicyService)]

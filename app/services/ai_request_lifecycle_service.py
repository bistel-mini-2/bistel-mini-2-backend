from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.recommendation_graph import RecommendationGraphRunner
from app.ai.agents.condition_agent import ConditionAgent
from app.common.ai_status import (
    AssessmentStatus,
    UserStatus,
    map_assessment_to_user_status,
)
from app.common.exceptions import AppException, ErrorCode
from app.common.psycopg_pool_conf import psycopg_pool
from app.db.models.policy import Policy
from app.repositories.ai_request_repository import AiRequestModel, AiRequestRepository
from app.repositories.family_profile_repository import FamilyProfileRepository
from app.repositories.policy_assessment_repository import PolicyAssessmentRepository
from app.repositories.user_repository import UserRepository
from app.schemas.ai_contract import AssessmentInput, ConditionInput, RequestStatus
from app.schemas.ai_request_schema import (
    AiRequestSnapshot,
    EligibilityCriteriaItem,
    EligibilityFollowUpQuestionItem,
    EligibilityResultResponse,
    FollowUpQuestionItem,
    RecommendationEvidenceItem,
    RecommendationPollingResponse,
    RecommendationPollingStatus,
    RecommendationResultItem,
)
from app.services.policy_assessment_service import (
    ASSESSMENT_TYPE_ELIGIBILITY,
    PolicyAssessmentService,
)
from app.services.recommendation_result_normalizer import (
    normalize_recommendation_result_item,
    normalize_recommendation_result_json,
)
from app.services.recommendation_service import RecommendationService


RECOMMENDATION_RESULT_SOURCE_TYPE = "RECOMMENDATION_RESULT"
TERMINAL_REQUEST_STATUSES = {
    RequestStatus.COMPLETED,
    RequestStatus.FOLLOW_UP_REQUIRED,
    RequestStatus.FAILED,
    RequestStatus.CANCELED,
}


class AiRequestLifecycleService:
    def __init__(
        self,
        repository: AiRequestRepository | None = None,
        assessment_repository: PolicyAssessmentRepository | None = None,
        assessment_service: PolicyAssessmentService | None = None,
        condition_agent: ConditionAgent | None = None,
        recommendation_graph: RecommendationGraphRunner | None = None,
        recommendation_service: RecommendationService | None = None,
    ) -> None:
        self.repository = repository or AiRequestRepository()
        self.assessment_repository = assessment_repository or PolicyAssessmentRepository()
        self.assessment_service = assessment_service or PolicyAssessmentService()
        self.condition_agent = condition_agent or ConditionAgent()
        self.recommendation_graph = recommendation_graph
        self.recommendation_service = recommendation_service

    async def create_request(
        self,
        db: AsyncSession,
        user_id: int,
        request_type: str = "recommendation",
        source_type: str = "FORM",
        source_ref_id: str | None = None,
        raw_query: str | None = None,
        selected_conditions: dict[str, Any] | None = None,
        policy_id: int | None = None,
    ) -> AiRequestSnapshot:
        await self._ensure_user_exists(db, user_id)
        request = await self.repository.create(
            db=db,
            request_type=request_type,
            user_id=user_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            raw_query=raw_query,
            selected_conditions=selected_conditions,
            policy_id=policy_id,
        )
        return self.to_snapshot(request_type, request)

    async def create_eligibility_request(
        self,
        db: AsyncSession,
        user_id: int,
        policy_identifier: int | str,
        source_type: str = "POLICY_DETAIL",
        source_ref_id: str | None = None,
        raw_query: str | None = None,
        selected_conditions: dict[str, Any] | None = None,
    ) -> AiRequestSnapshot:
        return await self.create_request(
            db=db,
            user_id=user_id,
            request_type="eligibility",
            source_type=source_type,
            source_ref_id=source_ref_id,
            raw_query=raw_query,
            selected_conditions=selected_conditions,
            policy_id=await self.resolve_policy_id(db, policy_identifier),
        )

    async def mark_processing(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestSnapshot:
        return await self._set_status(
            db,
            request_type,
            request_id,
            RequestStatus.PROCESSING,
        )

    async def mark_completed(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestSnapshot:
        return await self._set_status(
            db,
            request_type,
            request_id,
            RequestStatus.COMPLETED,
        )

    async def mark_follow_up_required(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestSnapshot:
        return await self._set_status(
            db,
            request_type,
            request_id,
            RequestStatus.FOLLOW_UP_REQUIRED,
        )

    async def mark_failed(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
        error_message: str | None = None,
    ) -> AiRequestSnapshot:
        return await self._set_status(
            db,
            request_type,
            request_id,
            RequestStatus.FAILED,
            error_message=error_message,
        )

    async def cancel_request(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
        user_id: int,
    ) -> AiRequestSnapshot:
        request = await self._get_request_or_raise(db, request_type, request_id)
        if request.user_id != user_id:
            raise self._not_found(request_type, request_id)

        request_status = RequestStatus(request.request_status)
        if request_status in TERMINAL_REQUEST_STATUSES:
            return self.to_snapshot(request_type, request)

        request = await self.repository.update_status(
            db=db,
            request=request,
            status=RequestStatus.CANCELED,
            error_message=None,
        )
        return self.to_snapshot(request_type, request)

    async def get_request(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
        user_id: int | None = None,
    ) -> AiRequestSnapshot:
        request = await self._get_request_or_raise(db, request_type, request_id)
        if user_id is not None and request.user_id != user_id:
            raise self._not_found(request_type, request_id)
        return self.to_snapshot(request_type, request)

    async def get_recommendation_polling_result(
        self,
        db: AsyncSession,
        request_id: int,
        user_id: int,
    ) -> RecommendationPollingResponse:
        request = await self._get_request_or_raise(db, "recommendation", request_id)
        if request.user_id != user_id:
            raise self._not_found("recommendation", request_id)
        return self.to_recommendation_polling_response(request)

    async def get_eligibility_result(
        self,
        db: AsyncSession,
        request_id: int,
        user_id: int,
    ) -> EligibilityResultResponse:
        request = await self._get_request_or_raise(db, "eligibility", request_id)
        if request.user_id != user_id:
            raise self._not_found("eligibility", request_id)

        policy = await self._get_policy_summary_or_raise(db, int(request.policy_id))
        assessment = await self.assessment_repository.find_eligibility_assessment(
            db=db,
            request_id=request_id,
            policy_id=int(request.policy_id),
        )
        return self.to_eligibility_result_response(
            request=request,
            policy=policy,
            assessment=assessment,
        )

    async def process_condition_request(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestSnapshot:
        request = await self._get_request_or_raise(db, request_type, request_id)
        if self._is_canceled(request):
            return self.to_snapshot(request_type, request)

        parsed_query_json = request.parsed_query_json or {}
        profile_snapshot = await self._condition_profile_snapshot(
            db=db,
            request_type=request_type,
            request=request,
        )
        if await self._is_request_canceled(db, request_type, request_id):
            return self.to_snapshot(request_type, request)

        condition_result = await self.condition_agent.analyze(
            ConditionInput(
                raw_query=request.raw_query,
                selected_conditions=parsed_query_json.get("selected_conditions"),
                profile_snapshot=profile_snapshot,
            )
        )
        if await self._is_request_canceled(db, request_type, request_id):
            return self.to_snapshot(request_type, request)

        input_issues_json = [
            issue.model_dump(mode="json")
            for issue in condition_result.input_issues
        ]
        profile_conflict_json = [
            conflict.model_dump(mode="json")
            for conflict in condition_result.profile_conflicts
        ]
        parsed_result = {
            **condition_result.parsed_query_json,
            "input_issues": input_issues_json,
            "questions": [
                candidate.model_dump(mode="json")
                for candidate in condition_result.follow_up_candidates
            ],
        }
        await self.repository.update_payload(
            db=db,
            request=request,
            parsed_query_json=parsed_result,
            merged_condition_json=condition_result.merged_condition_json,
            profile_conflict_json=profile_conflict_json,
        )
        if await self._is_request_canceled(db, request_type, request_id):
            return self.to_snapshot(request_type, request)

        if condition_result.follow_up_candidates:
            return await self.mark_follow_up_required(
                db,
                request_type,
                request_id,
            )
        if request_type == "recommendation":
            result_json = await self._recommendation_graph().run(
                db=db,
                request_id=request_id,
                merged_condition_json=condition_result.merged_condition_json,
                input_issues=input_issues_json,
                profile_conflict_json=profile_conflict_json,
                raw_query=request.raw_query,
                selected_conditions=parsed_query_json.get("selected_conditions"),
            )
            result_json = normalize_recommendation_result_json(result_json)
            await self.repository.update_result(
                db=db,
                request=request,
                result_json=result_json,
            )
        elif request_type == "eligibility":
            await self._save_eligibility_assessment(
                db=db,
                request=request,
                request_id=request_id,
                merged_condition_json=condition_result.merged_condition_json,
                input_issues_json=input_issues_json,
                profile_conflict_json=profile_conflict_json,
            )
        if await self._is_request_canceled(db, request_type, request_id):
            return self.to_snapshot(request_type, request)

        return await self.mark_completed(db, request_type, request_id)

    async def _save_eligibility_assessment(
        self,
        db: AsyncSession,
        request: AiRequestModel,
        request_id: int,
        merged_condition_json: dict[str, Any],
        input_issues_json: list[dict[str, Any]],
        profile_conflict_json: list[dict[str, Any]],
    ) -> None:
        policy_id = int(request.policy_id)
        assessment_condition = {
            **merged_condition_json,
            "input_issues": input_issues_json,
            "profile_conflicts": profile_conflict_json,
        }
        evidence_chunks = await self.assessment_repository.find_policy_evidence_chunks(
            db=db,
            policy_id=policy_id,
        )
        assessment_result = self.assessment_service.assess(
            AssessmentInput(
                policy_id=policy_id,
                merged_condition_json=assessment_condition,
                evidence_chunks=evidence_chunks,
            ),
            assessment_type=ASSESSMENT_TYPE_ELIGIBILITY,
        )[0]

        async with psycopg_pool.connection() as conn:
            await PolicyAssessmentRepository.save_assessment(
                conn=conn,
                result=assessment_result,
                assessment_type=ASSESSMENT_TYPE_ELIGIBILITY,
                eligibility_request_id=request_id,
            )

    async def resolve_policy_id(
        self,
        db: AsyncSession,
        policy_identifier: int | str,
    ) -> int:
        if isinstance(policy_identifier, int):
            return await self._get_policy_id_or_raise(db, policy_identifier)

        stripped = policy_identifier.strip()
        if stripped.isdecimal():
            return await self._get_policy_id_or_raise(db, int(stripped))

        result = await db.execute(
            select(Policy.policy_id).where(Policy.policy_code == stripped)
        )
        policy_id = result.scalar_one_or_none()
        if policy_id is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message=f"Policy not found: {policy_identifier}",
            )
        return int(policy_id)

    async def _set_status(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
        request_status: RequestStatus,
        error_message: str | None = None,
    ) -> AiRequestSnapshot:
        request = await self._get_request_or_raise(db, request_type, request_id)
        request = await self.repository.update_status(
            db=db,
            request=request,
            status=request_status,
            error_message=error_message,
        )
        return self.to_snapshot(request_type, request)

    async def _get_request_or_raise(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> AiRequestModel:
        request = await self.repository.find_by_id(db, request_type, request_id)
        if request is None:
            raise self._not_found(request_type, request_id)
        return request

    async def _is_request_canceled(
        self,
        db: AsyncSession,
        request_type: str,
        request_id: int,
    ) -> bool:
        request = await self._get_request_or_raise(db, request_type, request_id)
        if hasattr(db, "refresh"):
            await db.refresh(request)
        return self._is_canceled(request)

    @staticmethod
    def _is_canceled(request: AiRequestModel) -> bool:
        return RequestStatus(request.request_status) == RequestStatus.CANCELED

    def to_snapshot(
        self,
        request_type: str,
        request: AiRequestModel,
    ) -> AiRequestSnapshot:
        parsed_query_json = request.parsed_query_json or {}
        result_json = (
            request.result_json
            if hasattr(request, "result_json") and request.result_json is not None
            else {}
        )
        if request_type == "recommendation":
            result_json = normalize_recommendation_result_json(result_json)
        results = list(result_json.get("results") or [])
        recommendations = list(result_json.get("recommendations") or results)
        return AiRequestSnapshot(
            request_id=str(request.request_id),
            request_type=request_type,  # type: ignore[arg-type]
            status=RequestStatus(request.request_status),
            policy_id=(
                str(request.policy_id)
                if hasattr(request, "policy_id") and request.policy_id is not None
                else None
            ),
            source_type=request.source_type,
            source_ref_id=request.source_ref_id,
            parsed_query_json=parsed_query_json,
            merged_condition_json=request.merged_condition_json or {},
            profile_conflict_json=request.profile_conflict_json or [],
            result_json=result_json,
            results=results,
            recommendations=recommendations,
            questions=list(parsed_query_json.get("questions") or []),
            input_issues=list(parsed_query_json.get("input_issues") or []),
            error_message=request.error_message,
        )

    def to_recommendation_polling_response(
        self,
        request: AiRequestModel,
    ) -> RecommendationPollingResponse:
        request_status = RequestStatus(request.request_status)
        polling_status = self._polling_status(request_status)
        follow_up_questions = (
            self._follow_up_questions(request.parsed_query_json or {})
            if request_status == RequestStatus.FOLLOW_UP_REQUIRED
            else []
        )
        results = (
            self._recommendation_results(
                normalize_recommendation_result_json(
                    request.result_json
                    if hasattr(request, "result_json")
                    and request.result_json is not None
                    else {}
                )
            )
            if request_status == RequestStatus.COMPLETED
            else []
        )
        return RecommendationPollingResponse(
            request_id=str(request.request_id),
            status=polling_status,
            results=results,
            recommendations=results,
            follow_up_questions=follow_up_questions,
            error_message=(
                request.error_message if request_status == RequestStatus.FAILED else None
            ),
        )

    def to_eligibility_result_response(
        self,
        request: AiRequestModel,
        policy: dict[str, Any],
        assessment: dict[str, Any] | None,
    ) -> EligibilityResultResponse:
        request_status = RequestStatus(request.request_status)
        questions = self._eligibility_follow_up_questions(request.parsed_query_json or {})
        input_summary = self._eligibility_input_summary(request)

        if assessment is None:
            return EligibilityResultResponse(
                request_id=str(request.request_id),
                status=request_status,
                policy_id=str(request.policy_id),
                slug=str(policy["policy_code"]),
                policy_name=str(policy["policy_name"]),
                questions=questions,
                follow_up_questions=questions,
                input_summary=input_summary,
                error_message=(
                    request.error_message if request_status == RequestStatus.FAILED else None
                ),
            )

        assessment_status = AssessmentStatus(str(assessment["assessment_status"]))
        user_status = map_assessment_to_user_status(assessment_status)
        matched_conditions = self._string_list(assessment.get("matched_conditions_json"))
        missing_conditions = self._string_list(assessment.get("missing_conditions_json"))
        conflicting_conditions = self._string_list(
            assessment.get("conflicting_conditions_json")
        )
        manual_check_points = self._string_list(
            assessment.get("manual_check_points_json")
        )

        return EligibilityResultResponse(
            request_id=str(request.request_id),
            status=request_status,
            policy_id=str(request.policy_id),
            slug=str(policy["policy_code"]),
            policy_name=str(policy["policy_name"]),
            user_status=user_status.value,
            banner_level=self._banner_level(user_status),
            summary=assessment.get("reason_summary"),
            criteria=self._eligibility_criteria(
                assessment_status=assessment_status,
                reason_summary=assessment.get("reason_summary"),
                matched_conditions=matched_conditions,
                missing_conditions=missing_conditions,
                conflicting_conditions=conflicting_conditions,
                manual_check_points=manual_check_points,
            ),
            matched_conditions=matched_conditions,
            missing_conditions=missing_conditions,
            conflicting_conditions=conflicting_conditions,
            manual_check_points=manual_check_points,
            evidences=[
                self._eligibility_evidence_item(evidence, request.policy_id)
                for evidence in assessment.get("evidences", [])
                if isinstance(evidence, dict)
            ],
            questions=questions,
            follow_up_questions=questions,
            input_summary=input_summary,
            error_message=(
                request.error_message if request_status == RequestStatus.FAILED else None
            ),
        )

    def _polling_status(
        self,
        request_status: RequestStatus,
    ) -> RecommendationPollingStatus:
        if request_status in {RequestStatus.READY, RequestStatus.PROCESSING}:
            return "loading"
        if request_status in {
            RequestStatus.COMPLETED,
            RequestStatus.FOLLOW_UP_REQUIRED,
        }:
            return "done"
        return "error"

    def _recommendation_results(
        self,
        result_json: dict[str, Any],
    ) -> list[RecommendationResultItem]:
        raw_results = result_json.get("results") or result_json.get("recommendations")
        if not isinstance(raw_results, list):
            return []
        return [
            self._recommendation_result_item(item)
            for item in raw_results
            if isinstance(item, dict)
        ]

    def _recommendation_result_item(
        self,
        item: dict[str, Any],
    ) -> RecommendationResultItem:
        item = normalize_recommendation_result_item(item)
        raw_evidences = item.get("evidence") or item.get("evidences") or []
        evidences = [
            self._recommendation_evidence_item(evidence, item.get("policy_id"))
            for evidence in raw_evidences
            if isinstance(evidence, dict)
        ]
        normalized_item = {
            key: value
            for key, value in item.items()
            if key not in {"evidence", "evidences", "follow_up_questions"}
        }
        normalized_item.update(
            {
                "policy_id": str(item.get("policy_id") or ""),
                "policy_name": str(item.get("policy_name") or ""),
                "summary": str(
                    item.get("summary") or item.get("benefit_summary") or ""
                ),
                "match_score": self._to_float_or_none(item.get("match_score")),
                "evidence": evidences,
                "evidences": evidences,
                "raw_evidences": list(item.get("raw_evidences") or []),
                "follow_up_questions": [],
            }
        )
        return RecommendationResultItem.model_validate(normalized_item)

    def _recommendation_evidence_item(
        self,
        item: dict[str, Any],
        fallback_policy_id: Any,
    ) -> RecommendationEvidenceItem:
        return RecommendationEvidenceItem(
            chunk_id=item.get("chunk_id") or "",
            policy_id=item.get("policy_id") or fallback_policy_id or "",
            snippet=str(item.get("snippet") or ""),
            source_title=str(item.get("source_title") or ""),
            source_url=str(item.get("source_url") or ""),
            score=self._to_float_or_none(item.get("score")),
            evidence_role=item.get("evidence_role"),
        )

    def _follow_up_questions(
        self,
        parsed_query_json: dict[str, Any],
    ) -> list[FollowUpQuestionItem]:
        questions = parsed_query_json.get("questions") or []
        if not isinstance(questions, list):
            return []
        return [
            FollowUpQuestionItem(
                field_name=str(question.get("field_name") or ""),
                question_text=str(question.get("question_text") or ""),
                reason=question.get("reason"),
                priority=int(question.get("priority") or 0),
            )
            for question in questions
            if isinstance(question, dict)
        ][:2]

    def _eligibility_follow_up_questions(
        self,
        parsed_query_json: dict[str, Any],
    ) -> list[EligibilityFollowUpQuestionItem]:
        questions = parsed_query_json.get("questions") or []
        if not isinstance(questions, list):
            return []
        return [
            EligibilityFollowUpQuestionItem(
                follow_up_id=(
                    str(question.get("follow_up_id"))
                    if question.get("follow_up_id") is not None
                    else str(index + 1)
                ),
                field_name=str(question.get("field_name") or ""),
                question_text=str(question.get("question_text") or ""),
                reason=question.get("reason"),
                priority=int(question.get("priority") or 0),
            )
            for index, question in enumerate(questions[:2])
            if isinstance(question, dict)
        ]

    def _eligibility_input_summary(self, request: AiRequestModel) -> dict[str, Any]:
        parsed_query_json = request.parsed_query_json or {}
        selected_conditions = parsed_query_json.get("selected_conditions")
        if isinstance(selected_conditions, dict):
            return selected_conditions
        return request.merged_condition_json or {}

    def _eligibility_criteria(
        self,
        assessment_status: AssessmentStatus,
        reason_summary: str | None,
        matched_conditions: list[str],
        missing_conditions: list[str],
        conflicting_conditions: list[str],
        manual_check_points: list[str],
    ) -> list[EligibilityCriteriaItem]:
        criteria: list[EligibilityCriteriaItem] = []
        criteria.extend(
            EligibilityCriteriaItem(label=condition, status="ok", note="조건이 충족되었습니다.")
            for condition in matched_conditions
        )
        criteria.extend(
            EligibilityCriteriaItem(label=condition, status="check", note="추가 확인이 필요합니다.")
            for condition in missing_conditions
        )
        criteria.extend(
            EligibilityCriteriaItem(label=condition, status="check", note="입력값이 서로 충돌합니다.")
            for condition in conflicting_conditions
        )
        criteria.extend(
            EligibilityCriteriaItem(label=condition, status="check", note="수동 확인이 필요합니다.")
            for condition in manual_check_points
        )
        if not criteria and reason_summary:
            status_by_assessment = {
                AssessmentStatus.LIKELY_MATCH: "ok",
                AssessmentStatus.NOT_MATCH: "no",
            }
            criteria.append(
                EligibilityCriteriaItem(
                    label="판정 결과",
                    status=status_by_assessment.get(assessment_status, "check"),
                    note=reason_summary,
                )
            )
        return criteria

    def _eligibility_evidence_item(
        self,
        item: dict[str, Any],
        fallback_policy_id: Any,
    ) -> RecommendationEvidenceItem:
        evidence_role = item.get("evidence_role")
        return RecommendationEvidenceItem(
            chunk_id=item.get("chunk_id") or "",
            policy_id=item.get("evidence_policy_id") or fallback_policy_id or "",
            snippet=str(item.get("snippet") or ""),
            source_title=str(item.get("source_title") or ""),
            source_url=str(item.get("source_url") or ""),
            score=self._to_float_or_none(item.get("similarity_score")),
            evidence_role=(
                str(evidence_role).lower() if evidence_role is not None else None
            ),
        )

    def _banner_level(self, user_status: UserStatus) -> str:
        level_by_status = {
            UserStatus.RECOMMENDABLE: "high",
            UserStatus.NEEDS_CONFIRMATION: "mid",
            UserStatus.DIFFICULT_TO_RECOMMEND: "low",
        }
        return level_by_status[user_status]

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    def _to_float_or_none(self, value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _recommendation_graph(self) -> RecommendationGraphRunner:
        if self.recommendation_graph is None:
            self.recommendation_graph = RecommendationGraphRunner(
                recommendation_service=self.recommendation_service
            )
        return self.recommendation_graph

    async def _ensure_user_exists(self, db: AsyncSession, user_id: int) -> None:
        user = await UserRepository.find_by_id(db, user_id)
        if user is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message=f"User not found: {user_id}",
            )

    async def _get_policy_id_or_raise(self, db: AsyncSession, policy_id: int) -> int:
        result = await db.execute(
            select(Policy.policy_id).where(Policy.policy_id == policy_id)
        )
        existing_policy_id = result.scalar_one_or_none()
        if existing_policy_id is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message=f"Policy not found: {policy_id}",
            )
        return int(existing_policy_id)

    async def _get_policy_summary_or_raise(
        self,
        db: AsyncSession,
        policy_id: int,
    ) -> dict[str, Any]:
        result = await db.execute(
            select(Policy.policy_id, Policy.policy_code, Policy.policy_name).where(
                Policy.policy_id == policy_id
            )
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.POLICY_NOT_FOUND,
                message=f"Policy not found: {policy_id}",
            )
        return dict(row)

    async def _profile_snapshot(
        self,
        db: AsyncSession,
        user_id: int,
    ) -> dict[str, Any] | None:
        profile = await FamilyProfileRepository.find_profile_by_user_id(db, user_id)
        if profile is None:
            return None

        snapshot = dict(profile.profile_json or {})
        snapshot.update(
            {
                "region_code": profile.region_code,
                "household_type": profile.household_type,
                "income_bracket": profile.income_bracket,
                "employment_status": profile.employment_status,
                "pregnancy_status": profile.pregnancy_status,
            }
        )
        return {
            key: value for key, value in snapshot.items() if value not in (None, "", [])
        }

    async def _condition_profile_snapshot(
        self,
        db: AsyncSession,
        request_type: str,
        request: AiRequestModel,
    ) -> dict[str, Any] | None:
        if (
            request_type == "eligibility"
            and request.source_type == RECOMMENDATION_RESULT_SOURCE_TYPE
        ):
            return None
        return await self._profile_snapshot(db, request.user_id)

    def _not_found(self, request_type: str, request_id: int) -> AppException:
        return AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message=f"AI request not found: {request_type}/{request_id}",
        )

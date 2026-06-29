import logging
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agents.eligibility_evidence_agent import (
    EligibilityEvidenceAgent,
    EligibilityEvidenceContext,
)
from app.ai.tools.policy_chunk_search_tool import search_policy_chunks
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
from app.schemas.ai_contract import (
    AssessmentInput,
    ConditionInput,
    EvidenceChunk,
    RequestStatus,
)
from app.schemas.ai_request_schema import (
    AiRequestSnapshot,
    EligibilityCriteriaItem,
    EligibilityFollowUpQuestionItem,
    EligibilityResultResponse,
    FollowUpQuestionItem,
    RecommendationAnswer,
    RecommendationEvidenceItem,
    RecommendationHistoryItem,
    RecommendationHistoryResponse,
    RecommendationPollingResponse,
    RecommendationPollingStatus,
    RecommendationResultItem,
)
from app.services.policy_assessment_service import (
    ASSESSMENT_TYPE_ELIGIBILITY,
    PolicyAssessmentService,
)
from app.services.policy_rule_filter_service import PolicyRuleFilterService
from app.services.recommendation_result_normalizer import (
    normalize_recommendation_result_item,
    normalize_recommendation_result_json,
)
from app.services.recommendation_service import RecommendationService


RECOMMENDATION_RESULT_SOURCE_TYPE = "RECOMMENDATION_RESULT"
AI_REQUEST_USER_ERROR_MESSAGE = (
    "분석 처리 중 일시적인 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)
logger = logging.getLogger(__name__)


class AiRequestLifecycleService:
    def __init__(
        self,
        repository: AiRequestRepository | None = None,
        assessment_repository: PolicyAssessmentRepository | None = None,
        assessment_service: PolicyAssessmentService | None = None,
        condition_agent: ConditionAgent | None = None,
        recommendation_graph: RecommendationGraphRunner | None = None,
        recommendation_service: RecommendationService | None = None,
        rule_filter_service: PolicyRuleFilterService | None = None,
        policy_chunk_searcher: Any | None = None,
        eligibility_evidence_agent: EligibilityEvidenceAgent | None = None,
    ) -> None:
        self.repository = repository or AiRequestRepository()
        self.assessment_repository = assessment_repository or PolicyAssessmentRepository()
        self.assessment_service = assessment_service or PolicyAssessmentService()
        self.condition_agent = condition_agent or ConditionAgent()
        self.recommendation_graph = recommendation_graph
        self.recommendation_service = recommendation_service
        self.rule_filter_service = rule_filter_service or PolicyRuleFilterService()
        self.policy_chunk_searcher = policy_chunk_searcher or search_policy_chunks
        self.eligibility_evidence_agent = (
            eligibility_evidence_agent or EligibilityEvidenceAgent()
        )

    async def create_request(
        self,
        db: AsyncSession,
        user_id: int,
        request_type: str = "recommendation",
        source_type: str = "FORM",
        source_ref_id: str | None = None,
        raw_query: str | None = None,
        selected_conditions: dict[str, Any] | None = None,
        follow_up_resolved: bool = False,
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
            follow_up_resolved=follow_up_resolved,
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

    async def submit_recommendation_answers(
        self,
        db: AsyncSession,
        request_id: int,
        user_id: int,
        answers: list[dict[str, Any]],
    ) -> AiRequestSnapshot:
        """추가질문 답변을 받아 조건에 반영하고 추천을 재실행 가능 상태로 만든다.

        답변을 raw_query에 자연어로 머지해 condition_agent가 재파싱하게 하고,
        follow_up_resolved 플래그를 set해 재실행 시 게이트가 다시 발생하지 않게 한다.
        빈 답변(건너뛰기)이면 플래그만 set하고 그대로 재실행한다.
        """
        request = await self._get_request_or_raise(db, "recommendation", request_id)
        if request.user_id != user_id:
            raise self._not_found("recommendation", request_id)
        # 추가질문 답변은 게이트 상태(FOLLOW_UP_REQUIRED)에서만 허용한다.
        # 이미 완료/처리중/실패한 요청에 답변이 와도 파이프라인을 다시 돌리지 않는다.
        if RequestStatus(request.request_status) != RequestStatus.FOLLOW_UP_REQUIRED:
            raise AppException(
                status_code=status.HTTP_409_CONFLICT,
                code=ErrorCode.CONFLICT,
                message="추가 정보가 필요한 요청에만 답변을 제출할 수 있어요.",
            )

        parsed_query_json = dict(request.parsed_query_json or {})
        parsed_query_json["follow_up_resolved"] = True
        # 답변을 구조화 저장(이력에서 Q/A로 보여주기 위함). 빈 답변은 제외.
        parsed_query_json["follow_up_answers"] = [
            {
                "question_text": " ".join(
                    str(answer.get("question_text") or "").split()
                ),
                "answer": " ".join(str(answer.get("answer") or "").split()),
            }
            for answer in answers
            if isinstance(answer, dict)
            and str(answer.get("answer") or "").strip()
        ]
        augmented_raw_query = self._augment_raw_query(request.raw_query, answers)
        await self.repository.update_payload(
            db=db,
            request=request,
            parsed_query_json=parsed_query_json,
            raw_query=augmented_raw_query,
        )
        return await self.mark_processing(db, "recommendation", request_id)

    def _augment_raw_query(
        self,
        raw_query: str | None,
        answers: list[dict[str, Any]],
    ) -> str:
        base = " ".join(str(raw_query or "").split())
        extra_parts: list[str] = []
        for answer in answers if isinstance(answers, list) else []:
            if not isinstance(answer, dict):
                continue
            question = " ".join(str(answer.get("question_text") or "").split())
            value = " ".join(str(answer.get("answer") or "").split())
            if not value:
                continue
            extra_parts.append(f"{question} {value}".strip() if question else value)
        extra = " ".join(extra_parts).strip()
        if not extra:
            return base
        return f"{base} {extra}".strip()

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
            error_message=AI_REQUEST_USER_ERROR_MESSAGE if error_message else None,
        )

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

    async def get_recommendation_history(
        self,
        db: AsyncSession,
        user_id: int,
        limit: int = 20,
    ) -> RecommendationHistoryResponse:
        """사용자의 완료된 추천 요청을 요약 목록으로 반환한다(이력)."""
        requests = await self.repository.list_completed_by_user(
            db=db,
            request_type="recommendation",
            user_id=user_id,
            limit=limit,
        )
        return RecommendationHistoryResponse(
            items=[self._recommendation_history_item(request) for request in requests]
        )

    def _recommendation_history_item(
        self,
        request: AiRequestModel,
    ) -> RecommendationHistoryItem:
        result_json = normalize_recommendation_result_json(
            request.result_json
            if hasattr(request, "result_json") and request.result_json is not None
            else {}
        )
        results = result_json.get("results") or result_json.get("recommendations") or []
        results = [item for item in results if isinstance(item, dict)]
        # 추천된 정책명 전부(카드에 칩으로 모두 노출). 안전 상한만 둔다.
        policy_names = [
            str(item.get("policy_name") or "").strip()
            for item in results[:12]
            if str(item.get("policy_name") or "").strip()
        ]
        created_at = getattr(request, "created_at", None)
        return RecommendationHistoryItem(
            request_id=str(request.request_id),
            created_at=created_at.isoformat() if created_at is not None else None,
            summary=self._history_summary(request),
            policy_count=len(results),
            top_policy_names=policy_names,
            follow_up_answers=self._history_follow_up_answers(request),
        )

    def _history_follow_up_answers(
        self,
        request: AiRequestModel,
    ) -> list[RecommendationAnswer]:
        # 추가질문 게이트에서 받은 답변(있으면)을 Q/A로 노출한다.
        parsed = getattr(request, "parsed_query_json", None) or {}
        rows = parsed.get("follow_up_answers") if isinstance(parsed, dict) else None
        answers: list[RecommendationAnswer] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            answer_text = " ".join(str(row.get("answer") or "").split())
            if not answer_text:
                continue
            answers.append(
                RecommendationAnswer(
                    question_text=" ".join(
                        str(row.get("question_text") or "").split()
                    ),
                    answer=answer_text,
                )
            )
        return answers

    # 이력 요약용 조건 코드 → 사용자 친화 라벨.
    _HISTORY_CHILD_AGE_LABELS = {
        "preborn": "임신 중",
        "0": "0세",
        "1": "1세",
        "2-5": "2~5세",
        "6-12": "6~12세",
        "13+": "13세 이상",
    }
    _HISTORY_STAGE_LABELS = {
        "pregnant": "임신 중",
        "newborn": "신생아",
        "infant": "영유아",
        "child": "아동",
        "teen": "청소년",
    }
    _HISTORY_INCOME_LABELS = {
        "low": "소득 50% 이하",
        "mid1": "소득 51~100%",
        "mid2": "소득 101~150%",
        "high": "소득 150% 초과",
    }
    _HISTORY_REGION_LABELS = {
        "national": "전국",
        "seoul": "서울",
        "busan": "부산",
        "daegu": "대구",
        "incheon": "인천",
        "gwangju": "광주",
        "daejeon": "대전",
        "ulsan": "울산",
        "sejong": "세종",
        "gyeonggi": "경기",
        "gangwon": "강원",
        "chungbuk": "충북",
        "chungnam": "충남",
        "jeonbuk": "전북",
        "jeonnam": "전남",
        "gyeongbuk": "경북",
        "gyeongnam": "경남",
        "jeju": "제주",
    }
    _HISTORY_SPECIAL_LABELS = {
        "single": "한부모",
        "multi": "다문화",
        "disabled": "장애",
        "many": "다자녀",
        "dual": "맞벌이",
        "low_income": "저소득",
        "veteran": "보훈",
    }

    def _history_summary(self, request: AiRequestModel) -> str:
        # 제목용 입력 요약: 폼 입력값(조건)에서 자녀 나이·소득·지역·특수상황을 만든다.
        # raw_query는 추가질문 답변이 덧붙어 지저분하므로 제목에는 쓰지 않는다.
        condition = self._history_condition(request)
        parts: list[str] = []

        child_age = str(condition.get("childAge") or condition.get("child_age") or "")
        stage = str(condition.get("stage") or condition.get("life_stage") or "")
        if child_age in self._HISTORY_CHILD_AGE_LABELS:
            parts.append(f"자녀 {self._HISTORY_CHILD_AGE_LABELS[child_age]}")
        elif stage in self._HISTORY_STAGE_LABELS:
            parts.append(self._HISTORY_STAGE_LABELS[stage])

        income = str(condition.get("income") or condition.get("income_level") or "")
        if income in self._HISTORY_INCOME_LABELS:
            parts.append(self._HISTORY_INCOME_LABELS[income])

        region = str(condition.get("region") or condition.get("region_code") or "")
        if region in self._HISTORY_REGION_LABELS:
            parts.append(self._HISTORY_REGION_LABELS[region])

        special = condition.get("special")
        special_values = special if isinstance(special, list) else [special]
        special_labels = [
            self._HISTORY_SPECIAL_LABELS[str(value)]
            for value in special_values
            if str(value) in self._HISTORY_SPECIAL_LABELS
        ]
        if special_labels:
            parts.append(" · ".join(special_labels))

        summary = " · ".join(parts)
        return summary[:80] if summary else "입력하신 가족 상황 기반 추천"

    def _history_condition(self, request: AiRequestModel) -> dict[str, Any]:
        # 정규화된 merged_condition_json 우선, 없으면 parsed_query_json.selected_conditions.
        merged = getattr(request, "merged_condition_json", None)
        if isinstance(merged, dict) and merged:
            return merged
        parsed = getattr(request, "parsed_query_json", None) or {}
        selected = parsed.get("selected_conditions") if isinstance(parsed, dict) else None
        return selected if isinstance(selected, dict) else {}

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
        parsed_query_json = request.parsed_query_json or {}
        selected_conditions = parsed_query_json.get("selected_conditions")
        manual_confirmations = self._manual_confirmations(selected_conditions)
        # 추가질문 게이트를 이미 한 번 거쳤는지(답변 후 재실행인지). 재실행이면 게이트 스킵.
        follow_up_resolved = bool(parsed_query_json.get("follow_up_resolved")) or bool(
            manual_confirmations
        )
        # 게이트에서 받은 답변(이력 표시용)은 재실행 후에도 보존한다.
        follow_up_answers = parsed_query_json.get("follow_up_answers") or []
        profile_snapshot = await self._condition_profile_snapshot(
            db=db,
            request_type=request_type,
            request=request,
        )
        condition_result = await self.condition_agent.analyze(
            ConditionInput(
                raw_query=request.raw_query,
                selected_conditions=selected_conditions,
                profile_snapshot=profile_snapshot,
            )
        )
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
            "selected_conditions": (
                condition_result.parsed_query_json.get("selected_conditions")
                or selected_conditions
            ),
            "input_issues": input_issues_json,
            "questions": [
                candidate.model_dump(mode="json")
                for candidate in condition_result.follow_up_candidates
            ],
            # 재실행 시 게이트가 다시 발생하지 않도록 플래그/답변을 보존한다.
            "follow_up_resolved": follow_up_resolved,
            "follow_up_answers": follow_up_answers,
        }
        await self.repository.update_payload(
            db=db,
            request=request,
            parsed_query_json=parsed_result,
            merged_condition_json=condition_result.merged_condition_json,
            profile_conflict_json=profile_conflict_json,
        )
        # 입력 파싱 게이트: 아직 추가질문을 안 거쳤을 때만.
        if condition_result.follow_up_candidates and not follow_up_resolved:
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
                selected_conditions=selected_conditions,
            )
            result_json = normalize_recommendation_result_json(result_json)
            # AI 판정 게이트: 아직 추가질문을 안 거쳤고, 최종 결과에 공통 부족정보가 있으면
            # 결과를 확정하지 않고 질문을 띄운다(최대 1라운드).
            if not follow_up_resolved:
                gate_questions = self._recommendation_missing_questions(result_json)
                if gate_questions:
                    await self.repository.update_payload(
                        db=db,
                        request=request,
                        parsed_query_json={
                            **parsed_result,
                            "questions": gate_questions,
                        },
                    )
                    return await self.mark_follow_up_required(
                        db,
                        request_type,
                        request_id,
                    )
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
        policy_rules = await self._find_policy_rules(db, policy_id)
        rule_filter = self.rule_filter_service.filter(
            condition=assessment_condition,
            policy_rules=policy_rules,
        )
        assessment_condition = self._merge_rule_filter_result(
            assessment_condition,
            rule_filter,
        )
        assessment_condition = self._apply_manual_confirmations(
            assessment_condition,
            (request.parsed_query_json or {}).get("selected_conditions"),
        )
        evidence_chunks = await self._find_eligibility_evidence_chunks(
            db=db,
            policy_id=policy_id,
            condition=assessment_condition,
            request=request,
        )
        assessment_result = self.assessment_service.assess(
            AssessmentInput(
                policy_id=policy_id,
                merged_condition_json=assessment_condition,
                evidence_chunks=evidence_chunks,
            ),
            assessment_type=ASSESSMENT_TYPE_ELIGIBILITY,
        )[0]
        eligibility_result_json = await self._eligibility_result_json(
            request=request,
            assessment_result=assessment_result,
        )
        await self.repository.update_result(
            db=db,
            request=request,
            result_json=eligibility_result_json,
        )

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

    async def _find_policy_rules(
        self,
        db: AsyncSession,
        policy_id: int,
    ) -> list[dict[str, Any]]:
        finder = getattr(self.assessment_repository, "find_policy_rules", None)
        if finder is None:
            return []
        return await finder(db=db, policy_id=policy_id)

    async def _find_eligibility_evidence_chunks(
        self,
        db: AsyncSession,
        policy_id: int,
        condition: dict[str, Any],
        request: AiRequestModel,
    ) -> list[EvidenceChunk]:
        query = self._eligibility_evidence_query(condition, request)
        if query:
            try:
                chunks = await self.policy_chunk_searcher(
                    query=query,
                    policy_ids=[policy_id],
                    top_k=8,
                    evidence_role="TARGET",
                )
                if chunks:
                    return chunks
            except Exception:
                logger.exception(
                    "지원 가능성 판정 근거 RAG 검색 실패: request_id=%s policy_id=%s",
                    request.request_id,
                    policy_id,
                )

        return await self.assessment_repository.find_policy_evidence_chunks(
            db=db,
            policy_id=policy_id,
        )

    def _eligibility_evidence_query(
        self,
        condition: dict[str, Any],
        request: AiRequestModel,
    ) -> str:
        parts: list[str] = ["지원 가능성 판정 근거"]
        raw_query = getattr(request, "raw_query", None)
        if raw_query:
            parts.append(f"사용자 질문: {raw_query}")

        selected_conditions = (getattr(request, "parsed_query_json", None) or {}).get(
            "selected_conditions"
        )
        if isinstance(selected_conditions, dict):
            condition_text = self._condition_dict_text(selected_conditions)
            if condition_text:
                parts.append(f"사용자 입력 조건: {condition_text}")

        label_by_key = {
            "matched_conditions": "충족 조건",
            "missing_conditions": "부족한 조건",
            "rule_failures": "맞지 않는 조건",
            "conflicting_conditions": "충돌 조건",
            "manual_check_points": "추가 확인 조건",
        }
        for key, label in label_by_key.items():
            values = self._string_values(condition.get(key))
            if values:
                parts.append(f"{label}: {', '.join(values)}")

        if len(parts) == 1:
            fallback_text = self._condition_dict_text(condition)
            if fallback_text:
                parts.append(fallback_text)
        return "\n".join(parts)[:1200]

    def _condition_dict_text(self, value: dict[str, Any]) -> str:
        excluded_keys = {
            "input_issues",
            "profile_conflicts",
            "matched_conditions",
            "missing_conditions",
            "rule_failures",
            "conflicting_conditions",
            "manual_check_points",
        }
        parts = []
        for key, item in value.items():
            if key in excluded_keys or item in (None, "", []):
                continue
            parts.append(f"{key}={self._condition_value_text(item)}")
        return ", ".join(parts)

    def _condition_value_text(self, value: Any) -> str:
        if isinstance(value, dict):
            return ", ".join(
                f"{key}:{self._condition_value_text(item)}"
                for key, item in value.items()
                if item not in (None, "", [])
            )
        if isinstance(value, list):
            return ", ".join(
                self._condition_value_text(item)
                for item in value
                if item not in (None, "", [])
            )
        return str(value)

    def _string_values(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if item not in (None, "")]
        if value in (None, ""):
            return []
        return [str(value)]

    def _merge_rule_filter_result(
        self,
        condition: dict[str, Any],
        rule_filter: Any,
    ) -> dict[str, Any]:
        return {
            **condition,
            "matched_conditions": self._merge_string_values(
                condition.get("matched_conditions"),
                rule_filter.matched_conditions,
            ),
            "missing_conditions": self._merge_string_values(
                condition.get("missing_conditions"),
                rule_filter.missing_conditions,
            ),
            "rule_failures": self._merge_string_values(
                condition.get("rule_failures"),
                rule_filter.rule_failures,
            ),
            "manual_check_points": self._merge_string_values(
                condition.get("manual_check_points"),
                rule_filter.manual_check_points,
            ),
        }

    def _apply_manual_confirmations(
        self,
        condition: dict[str, Any],
        selected_conditions: Any,
    ) -> dict[str, Any]:
        confirmations = self._manual_confirmations(selected_conditions)
        if not confirmations:
            return condition

        manual_check_points = self._string_values(condition.get("manual_check_points"))
        matched_conditions = self._string_values(condition.get("matched_conditions"))
        rule_failures = self._string_values(condition.get("rule_failures"))

        for confirmation in confirmations:
            if not isinstance(confirmation, dict):
                continue

            question = self._clean_user_condition_text(confirmation.get("question"))
            answer = str(confirmation.get("answer") or "").strip().lower()
            if not question or self._is_internal_condition_text(question):
                continue

            manual_check_points = [
                item for item in manual_check_points if item != question
            ]
            if answer == "yes":
                matched_conditions.append(question)
            elif answer == "no":
                rule_failures.append(question)
            else:
                manual_check_points.append(question)

        return {
            **condition,
            "matched_conditions": self._deduplicate_strings(matched_conditions),
            "rule_failures": self._deduplicate_strings(rule_failures),
            "manual_check_points": self._deduplicate_strings(manual_check_points),
        }

    def _manual_confirmations(self, selected_conditions: Any) -> list[dict[str, Any]]:
        if not isinstance(selected_conditions, dict):
            return []
        confirmations = selected_conditions.get("manual_confirmations")
        if not isinstance(confirmations, list):
            return []
        return [
            confirmation
            for confirmation in confirmations
            if isinstance(confirmation, dict)
            and self._clean_user_condition_text(confirmation.get("question"))
            and str(confirmation.get("answer") or "").strip().lower()
            in {"yes", "no", "unknown"}
        ]

    @staticmethod
    def _merge_string_values(
        current_value: Any,
        next_values: list[str],
    ) -> list[str]:
        values: list[str] = []
        if isinstance(current_value, list):
            values.extend(str(value) for value in current_value if value)
        elif current_value:
            values.append(str(current_value))
        values.extend(str(value) for value in next_values if value)
        return list(dict.fromkeys(values))

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
            error_message=(
                AI_REQUEST_USER_ERROR_MESSAGE
                if request_status == RequestStatus.FAILED and error_message
                else error_message
            ),
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
            error_message=(
                AI_REQUEST_USER_ERROR_MESSAGE
                if RequestStatus(request.request_status) == RequestStatus.FAILED
                and request.error_message
                else None
            ),
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
                AI_REQUEST_USER_ERROR_MESSAGE
                if request_status == RequestStatus.FAILED and request.error_message
                else None
            ),
        )

    def to_eligibility_result_response(
        self,
        request: AiRequestModel,
        policy: dict[str, Any],
        assessment: dict[str, Any] | None,
    ) -> EligibilityResultResponse:
        request_status = RequestStatus(request.request_status)
        parsed_questions = self._eligibility_follow_up_questions(
            request.parsed_query_json or {}
        )
        input_summary = self._eligibility_input_summary(request)

        if assessment is None:
            return EligibilityResultResponse(
                request_id=str(request.request_id),
                status=request_status,
                policy_id=str(request.policy_id),
                slug=str(policy["policy_code"]),
                policy_name=str(policy["policy_name"]),
                questions=parsed_questions,
                follow_up_questions=parsed_questions,
                input_summary=input_summary,
                error_message=self._safe_error_message(request, request_status),
            )

        assessment_status = AssessmentStatus(str(assessment["assessment_status"]))
        user_status = map_assessment_to_user_status(assessment_status)
        matched_conditions = self._user_condition_list(
            assessment.get("matched_conditions_json")
        )
        missing_conditions = self._user_condition_list(
            assessment.get("missing_conditions_json")
        )
        conflicting_conditions = self._string_list(
            assessment.get("conflicting_conditions_json")
        )
        manual_check_points = self._user_condition_list(
            assessment.get("manual_check_points_json")
        )
        manual_questions = self._manual_check_follow_up_questions(
            manual_check_points,
            start_index=len(parsed_questions),
        )
        questions = self._deduplicate_follow_up_questions(
            [*parsed_questions, *manual_questions]
        )
        stored_evidences = self._stored_eligibility_evidences(request)

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
            evidences=self._eligibility_evidence_items(
                evidences=[
                    evidence
                    for evidence in assessment.get("evidences", [])
                    if isinstance(evidence, dict)
                ],
                stored_evidences=stored_evidences,
                fallback_policy_id=request.policy_id,
                assessment_status=assessment_status,
                matched_conditions=matched_conditions,
                missing_conditions=missing_conditions,
                conflicting_conditions=conflicting_conditions,
                manual_check_points=manual_check_points,
            ),
            questions=questions,
            follow_up_questions=questions,
            input_summary=input_summary,
            error_message=self._safe_error_message(request, request_status),
        )

    async def _eligibility_result_json(
        self,
        request: AiRequestModel,
        assessment_result: Any,
    ) -> dict[str, Any]:
        evidence_payloads = [
            {
                **evidence.model_dump(mode="json"),
                "display_text": self._eligibility_evidence_display_text(
                    item=evidence.model_dump(mode="json"),
                    assessment_status=assessment_result.assessment_status,
                    matched_conditions=assessment_result.matched_conditions,
                    missing_conditions=assessment_result.missing_conditions,
                    conflicting_conditions=assessment_result.conflicting_conditions,
                    manual_check_points=assessment_result.manual_check_points,
                ),
            }
            for evidence in assessment_result.evidences
        ]
        display_texts = await self.eligibility_evidence_agent.rewrite_display_texts(
            evidences=evidence_payloads,
            context=EligibilityEvidenceContext(
                assessment_status=assessment_result.assessment_status,
                matched_conditions=assessment_result.matched_conditions,
                missing_conditions=assessment_result.missing_conditions,
                conflicting_conditions=assessment_result.conflicting_conditions,
                manual_check_points=assessment_result.manual_check_points,
            ),
            policy_name="정책",
            user_conditions={
                **self._selected_conditions_dict(request),
                "summary": assessment_result.reason_summary,
                "user_status": assessment_result.user_status.value,
            },
        )
        for evidence, display_text in zip(evidence_payloads, display_texts):
            evidence["display_text"] = display_text
        return {
            "assessment_status": assessment_result.assessment_status.value,
            "user_status": assessment_result.user_status.value,
            "reason_summary": assessment_result.reason_summary,
            "evidences": evidence_payloads,
        }

    def _polling_status(
        self,
        request_status: RequestStatus,
    ) -> RecommendationPollingStatus:
        if request_status in {RequestStatus.READY, RequestStatus.PROCESSING}:
            return "loading"
        # 추가질문 게이트는 결과(done)와 구분해 내려, 프론트가 답변 폼을 띄울 수 있게 한다.
        if request_status == RequestStatus.FOLLOW_UP_REQUIRED:
            return "follow_up"
        if request_status == RequestStatus.COMPLETED:
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
                options=self._question_options(question),
            )
            for question in questions
            if isinstance(question, dict)
        ][:2]

    def _recommendation_missing_questions(
        self,
        result_json: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """최종 추천 결과의 부족 정보를 게이트 질문(최대 2개)으로 변환한다.

        AI 모델이 제한적이라 missing_information만 믿으면 핵심 자격(수급 자격 등)을
        자주 놓치므로, 룰 기반 확인사항(check_before_apply/manual_check_points)에서
        결정적 자격 축을 키워드로 감지해 결정론적으로 질문을 올린다.
        순서: 1) 결정적 자격 키워드  2) AI missing_information  3) 룰 핵심 조건 field 라벨.
        상위 결과부터 훑어 중복 제거 후 최대 2개만 채택한다.
        parsed_query_json.questions에 저장되어 기존 폴링 변환이 그대로 사용한다.
        """
        results = result_json.get("results") or result_json.get("recommendations") or []
        if not isinstance(results, list):
            return []

        seen: set[str] = set()
        questions: list[dict[str, Any]] = []

        def add_question(question_text: str) -> bool:
            text = " ".join(str(question_text or "").split())
            if not text or text in seen:
                return False
            seen.add(text)
            questions.append(
                {
                    "field_name": "",
                    "question_text": text,
                    "reason": "더 정확한 추천을 위해 확인이 필요해요.",
                    "priority": len(questions),
                }
            )
            return len(questions) >= 2

        # 1순위: 결정적 자격 축(수급 자격 등)을 룰 확인사항 텍스트에서 키워드로 감지.
        for item in results:
            if not isinstance(item, dict):
                continue
            check_text = self._item_check_text(item)
            for keywords, question in self._GATE_KEYWORD_QUESTIONS:
                if any(keyword in check_text for keyword in keywords):
                    if add_question(question):
                        return questions

        # 2순위: AI 판정이 명시한 부족 정보.
        for item in results:
            if not isinstance(item, dict):
                continue
            missing = item.get("missing_information")
            for info in missing if isinstance(missing, list) else []:
                label = " ".join(str(info or "").split())
                if label and add_question(f"{label}, 알려주시겠어요?"):
                    return questions

        # 3순위: 룰상 핵심 조건 부족(missing_conditions)을 field 라벨 질문으로 보강.
        for item in results:
            if not isinstance(item, dict):
                continue
            conditions = item.get("missing_conditions")
            for condition in conditions if isinstance(conditions, list) else []:
                if not isinstance(condition, dict):
                    continue
                field = str(condition.get("field") or condition.get("field_name") or "")
                label = self._GATE_FIELD_LABELS.get(field)
                if label and add_question(f"{label}, 알려주시겠어요?"):
                    return questions

        return questions

    def _item_check_text(self, item: dict[str, Any]) -> str:
        """후보의 룰 기반 확인사항 텍스트를 모은다(키워드 감지용)."""
        parts: list[str] = [str(item.get("check_before_apply") or "")]
        for key in ("manual_check_points", "missing_conditions"):
            rows = item.get(key)
            for row in rows if isinstance(rows, list) else []:
                if isinstance(row, dict):
                    parts.append(str(row.get("reason") or ""))
        return " ".join(parts)

    # 룰 확인사항 텍스트에 이 키워드가 보이면, 해당 결정적 자격을 게이트 질문으로 올린다.
    _GATE_KEYWORD_QUESTIONS = (
        (
            ("수급 자격", "수급 여부", "수급 가구", "기초생활", "차상위"),
            "기초생활·차상위 등 수급 자격이 있으신가요?",
        ),
        (("출생신고",), "자녀의 출생신고를 마치셨나요?"),
        (
            ("장애 정도", "장애 등급", "장애인 등록", "장애아"),
            "자녀(또는 가구원)의 장애 등록 여부를 알려주시겠어요?",
        ),
        (
            ("위기아동", "가정보호", "전문위탁", "전문가정위탁"),
            "위기아동 가정보호·전문위탁 대상에 해당하시나요?",
        ),
    )

    # 룰 부족 조건 field → 게이트 질문에 쓸 사용자 친화 라벨.
    _GATE_FIELD_LABELS = {
        "income": "가구 소득 구간",
        "income_level": "가구 소득 구간",
        "region": "거주 지역",
        "region_code": "거주 지역",
        "stage_or_childAge": "자녀 나이(또는 임신 여부)",
        "stage": "생애주기(임신/영유아 등)",
        "child_age": "자녀 나이",
        "childAge": "자녀 나이",
    }

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
                options=self._question_options(question),
            )
            for index, question in enumerate(questions[:2])
            if isinstance(question, dict)
        ]

    def _question_options(self, question: dict[str, Any]) -> list[dict[str, str]]:
        options = question.get("options")
        if not isinstance(options, list):
            return []

        normalized: list[dict[str, str]] = []
        for option in options:
            if isinstance(option, str):
                normalized.append({"value": option, "label": option})
            elif isinstance(option, dict):
                value = option.get("value") or option.get("code") or option.get("label")
                label = option.get("label") or option.get("name") or value
                if value and label:
                    normalized.append({"value": str(value), "label": str(label)})
        return normalized

    def _eligibility_input_summary(self, request: AiRequestModel) -> dict[str, Any]:
        return self._selected_conditions_dict(request) or (
            request.merged_condition_json or {}
        )

    def _selected_conditions_dict(self, request: AiRequestModel) -> dict[str, Any]:
        parsed_query_json = request.parsed_query_json or {}
        selected_conditions = parsed_query_json.get("selected_conditions")
        if isinstance(selected_conditions, dict):
            return selected_conditions
        return {}

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
            EligibilityCriteriaItem(
                label=self._condition_display_label(condition),
                status="ok",
                note="",
            )
            for condition in matched_conditions
        )
        criteria.extend(
            EligibilityCriteriaItem(
                label=self._condition_display_label(condition),
                status="check",
                note="추가 확인이 필요합니다.",
            )
            for condition in missing_conditions
        )
        criteria.extend(
            EligibilityCriteriaItem(
                label=self._condition_display_label(condition),
                status="check",
                note="입력값이 서로 충돌합니다.",
            )
            for condition in conflicting_conditions
        )
        if not criteria and reason_summary and not manual_check_points:
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
        assessment_status: AssessmentStatus | None = None,
        matched_conditions: list[str] | None = None,
        missing_conditions: list[str] | None = None,
        conflicting_conditions: list[str] | None = None,
        manual_check_points: list[str] | None = None,
    ) -> RecommendationEvidenceItem:
        evidence_role = item.get("evidence_role")
        return RecommendationEvidenceItem(
            chunk_id=item.get("chunk_id") or "",
            policy_id=item.get("evidence_policy_id") or fallback_policy_id or "",
            snippet=str(item.get("snippet") or ""),
            display_text=(
                str(item.get("display_text"))
                if item.get("display_text") not in (None, "")
                else self._eligibility_evidence_display_text(
                    item=item,
                    assessment_status=assessment_status,
                    matched_conditions=matched_conditions or [],
                    missing_conditions=missing_conditions or [],
                    conflicting_conditions=conflicting_conditions or [],
                    manual_check_points=manual_check_points or [],
                )
            ),
            source_title=str(item.get("source_title") or ""),
            source_url=str(item.get("source_url") or ""),
            score=self._to_float_or_none(item.get("similarity_score")),
            evidence_role=(
                str(evidence_role).lower() if evidence_role is not None else None
            ),
        )

    def _eligibility_evidence_items(
        self,
        evidences: list[dict[str, Any]],
        stored_evidences: dict[str, dict[str, Any]] | None,
        fallback_policy_id: Any,
        assessment_status: AssessmentStatus,
        matched_conditions: list[str],
        missing_conditions: list[str],
        conflicting_conditions: list[str],
        manual_check_points: list[str],
    ) -> list[RecommendationEvidenceItem]:
        result: list[RecommendationEvidenceItem] = []
        seen: set[str] = set()
        stored_evidences = stored_evidences or {}
        for evidence in evidences:
            stored = stored_evidences.get(str(evidence.get("chunk_id")))
            item = self._eligibility_evidence_item(
                {**evidence, **(stored or {})},
                fallback_policy_id,
                assessment_status=assessment_status,
                matched_conditions=matched_conditions,
                missing_conditions=missing_conditions,
                conflicting_conditions=conflicting_conditions,
                manual_check_points=manual_check_points,
            )
            key = "|".join(
                (
                    item.display_text or "",
                    item.source_url or "",
                    str(item.chunk_id or ""),
                )
            )
            text_key = " ".join(str(item.display_text or item.snippet or "").split())
            dedupe_key = text_key or key
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            result.append(item)
        return result[:5]

    def _stored_eligibility_evidences(
        self,
        request: AiRequestModel,
    ) -> dict[str, dict[str, Any]]:
        result_json = (
            request.result_json
            if hasattr(request, "result_json") and isinstance(request.result_json, dict)
            else {}
        )
        evidences = result_json.get("evidences")
        if not isinstance(evidences, list):
            return {}
        return {
            str(evidence.get("chunk_id")): evidence
            for evidence in evidences
            if isinstance(evidence, dict) and evidence.get("chunk_id") not in (None, "")
        }

    def _safe_error_message(
        self,
        request: AiRequestModel,
        request_status: RequestStatus,
    ) -> str | None:
        if request_status != RequestStatus.FAILED:
            return None
        return AI_REQUEST_USER_ERROR_MESSAGE

    def _user_condition_list(self, value: Any) -> list[str]:
        return [
            text
            for text in (self._clean_user_condition_text(item) for item in self._string_list(value))
            if text and not self._is_internal_condition_text(text)
        ]

    def _manual_check_follow_up_questions(
        self,
        manual_check_points: list[str],
        start_index: int = 0,
    ) -> list[EligibilityFollowUpQuestionItem]:
        questions: list[EligibilityFollowUpQuestionItem] = []
        for index, point in enumerate(manual_check_points):
            text = self._clean_user_condition_text(point)
            if not text or self._is_internal_condition_text(text):
                continue
            questions.append(
                EligibilityFollowUpQuestionItem(
                    follow_up_id=f"manual-{start_index + index + 1}",
                    field_name="manual_confirmation",
                    question_text=text,
                    reason="정확한 지원 가능성 판정을 위해 직접 확인이 필요해요.",
                    priority=start_index + index + 1,
                    options=[
                        {"value": "yes", "label": "예, 해당돼요"},
                        {"value": "no", "label": "아니요, 해당되지 않아요"},
                        {"value": "unknown", "label": "잘 모르겠어요"},
                    ],
                )
            )
        return questions

    def _deduplicate_follow_up_questions(
        self,
        questions: list[EligibilityFollowUpQuestionItem],
    ) -> list[EligibilityFollowUpQuestionItem]:
        seen: set[str] = set()
        result: list[EligibilityFollowUpQuestionItem] = []
        for question in questions:
            key = question.question_text.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(question)
        return result[:5]

    def _eligibility_evidence_display_text(
        self,
        item: dict[str, Any],
        assessment_status: AssessmentStatus | None = None,
        matched_conditions: list[str] | None = None,
        missing_conditions: list[str] | None = None,
        conflicting_conditions: list[str] | None = None,
        manual_check_points: list[str] | None = None,
    ) -> str:
        return self.eligibility_evidence_agent.display_text(
            evidence=item,
            context=EligibilityEvidenceContext(
                assessment_status=assessment_status,
                matched_conditions=matched_conditions or [],
                missing_conditions=missing_conditions or [],
                conflicting_conditions=conflicting_conditions or [],
                manual_check_points=manual_check_points or [],
            ),
        )

    def _clean_user_condition_text(self, value: Any) -> str:
        return self.eligibility_evidence_agent.clean_user_text(value)

    def _condition_display_label(self, value: Any) -> str:
        return self.eligibility_evidence_agent.condition_label(value)

    def _is_internal_condition_text(self, value: Any) -> bool:
        return self.eligibility_evidence_agent.is_internal_text(value)

    def _deduplicate_strings(self, values: list[str]) -> list[str]:
        return self.eligibility_evidence_agent.deduplicate_strings(values)

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

from collections.abc import AsyncGenerator
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.ai_request_controller as ai_request_controller
from app.api.ai_request_controller import eligibility_router
from app.common.exceptions import register_exception_handlers
from app.core.dependencies import get_current_user
from app.db.session import get_db_session
from app.schemas.ai_contract import ConditionResult, EvidenceChunk, RequestStatus
from app.schemas.ai_request_schema import EligibilityResultResponse
from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def test_create_eligibility_request_uses_common_lifecycle(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        db = SimpleNamespace(commit=lambda: None)

        async def commit():
            captured["committed"] = True

        db.commit = commit
        yield db

    async def fake_current_user() -> object:
        return SimpleNamespace(user_id=7)

    async def fake_create_eligibility_request(
        self,
        db,
        *,
        user_id,
        policy_identifier,
        source_type,
        source_ref_id,
        raw_query,
        selected_conditions,
    ):
        captured["create"] = {
            "user_id": user_id,
            "policy_identifier": policy_identifier,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "raw_query": raw_query,
            "selected_conditions": selected_conditions,
        }
        return SimpleNamespace(
            request_id="123",
            status=SimpleNamespace(value="READY"),
        )

    async def fake_mark_processing(self, db, request_type, request_id):
        captured["mark_processing"] = {
            "request_type": request_type,
            "request_id": request_id,
        }
        return SimpleNamespace(
            request_id=str(request_id),
            status=SimpleNamespace(value="PROCESSING"),
        )

    async def fake_process_ai_condition_request(
        request_type: str,
        request_id: int,
    ) -> None:
        captured["background"] = {
            "request_type": request_type,
            "request_id": request_id,
        }

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "create_eligibility_request",
        fake_create_eligibility_request,
    )
    monkeypatch.setattr(
        AiRequestLifecycleService,
        "mark_processing",
        fake_mark_processing,
    )
    monkeypatch.setattr(
        ai_request_controller,
        "process_ai_condition_request",
        fake_process_ai_condition_request,
    )

    app = FastAPI()
    app.include_router(eligibility_router)
    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)

    user_conditions = {
        "stage": "newborn",
        "child_age": "0",
        "income": "mid1",
        "region": "seoul",
        "special": ["many"],
    }
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/eligibility/requests",
            json={
                "policy_id": "WLF00000024",
                "user_conditions": user_conditions,
                "source_ref_id": "WLF00000024",
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert body["success"] is True
    assert body["data"]["request_id"] == "123"
    assert body["data"]["status"]["value"] == "PROCESSING"
    assert body["meta"] == {
        "request_id": "123",
        "follow_up_required": False,
    }
    assert captured["create"] == {
        "user_id": 7,
        "policy_identifier": "WLF00000024",
        "source_type": "POLICY_DETAIL",
        "source_ref_id": "WLF00000024",
        "raw_query": None,
        "selected_conditions": user_conditions,
    }
    assert captured["mark_processing"] == {
        "request_type": "eligibility",
        "request_id": 123,
    }
    assert captured["committed"] is True
    assert captured["background"] == {
        "request_type": "eligibility",
        "request_id": 123,
    }


def test_eligibility_request_inherits_recommendation_conditions(monkeypatch) -> None:
    import asyncio

    captured: dict[str, object] = {}
    source_request = SimpleNamespace(
        request_id=237,
        request_status=RequestStatus.COMPLETED.value,
        user_id=7,
        source_type="CHAT",
        source_ref_id=None,
        raw_query="추천",
        parsed_query_json={
            "selected_conditions": {
                "stage": "teen",
                "childAge": "2-5",
                "income": "mid1",
                "region": "seoul",
            }
        },
        merged_condition_json={"region": "seoul"},
        profile_conflict_json=[],
        result_json={},
        error_message=None,
    )

    class FakeRepository:
        async def create(
            self,
            db,
            request_type,
            user_id,
            source_type,
                source_ref_id=None,
                raw_query=None,
                selected_conditions=None,
                follow_up_resolved=False,
                policy_id=None,
            ):
                captured["selected_conditions"] = selected_conditions
                return SimpleNamespace(
                request_id=60,
                request_status=RequestStatus.READY.value,
                user_id=user_id,
                policy_id=policy_id,
                source_type=source_type,
                source_ref_id=source_ref_id,
                raw_query=raw_query,
                parsed_query_json={"selected_conditions": selected_conditions},
                merged_condition_json={},
                profile_conflict_json=[],
                result_json={},
                error_message=None,
            )

        async def find_by_id(self, db, request_type, request_id):
            captured["lookup"] = (request_type, request_id)
            return source_request

    async def fake_ensure_user_exists(self, db, user_id):
        captured["user_id"] = user_id

    async def fake_resolve_policy_id(self, db, policy_identifier):
        captured["policy_identifier"] = policy_identifier
        return int(policy_identifier)

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "_ensure_user_exists",
        fake_ensure_user_exists,
    )
    monkeypatch.setattr(
        AiRequestLifecycleService,
        "resolve_policy_id",
        fake_resolve_policy_id,
    )

    service = AiRequestLifecycleService(repository=FakeRepository())
    snapshot = asyncio.run(
        service.create_eligibility_request(
            db=SimpleNamespace(),
            user_id=7,
            policy_identifier=308,
            source_type="RECOMMENDATION_RESULT",
            source_ref_id="237",
            selected_conditions={},
        )
    )

    assert captured["lookup"] == ("recommendation", 237)
    assert captured["selected_conditions"] == {
        "stage": "teen",
        "childAge": "2-5",
        "income": "mid1",
        "region": "seoul",
    }
    assert snapshot.parsed_query_json["selected_conditions"] == captured["selected_conditions"]


def test_get_eligibility_request_returns_result_response(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        yield SimpleNamespace()

    async def fake_current_user() -> object:
        return SimpleNamespace(user_id=7)

    async def fake_get_eligibility_result(
        self,
        db,
        *,
        request_id,
        user_id,
    ):
        captured["get"] = {
            "request_id": request_id,
            "user_id": user_id,
        }
        return EligibilityResultResponse(
            request_id=str(request_id),
            status=RequestStatus.COMPLETED,
            policy_id="24",
            slug="WLF00000024",
            policy_name="테스트 정책",
            user_status="RECOMMENDABLE",
            banner_level="high",
            summary="지원 가능성이 높습니다.",
            matched_conditions=["region"],
            evidences=[],
        )

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "get_eligibility_result",
        fake_get_eligibility_result,
    )

    app = FastAPI()
    app.include_router(eligibility_router)
    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.get("/api/v1/eligibility/requests/123")

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["request_id"] == "123"
    assert body["data"]["status"] == "COMPLETED"
    assert body["data"]["slug"] == "WLF00000024"
    assert body["data"]["user_status"] == "RECOMMENDABLE"
    assert body["meta"] == {
        "request_id": "123",
        "follow_up_required": False,
    }
    assert captured["get"] == {
        "request_id": 123,
        "user_id": 7,
    }


def test_answer_eligibility_request_uses_same_lifecycle_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_db() -> AsyncGenerator[object, None]:
        db = SimpleNamespace(commit=lambda: None)

        async def commit():
            captured["committed"] = True

        db.commit = commit
        yield db

    async def fake_current_user() -> object:
        return SimpleNamespace(user_id=7)

    async def fake_answer_eligibility_follow_up(
        self,
        db,
        *,
        request_id,
        user_id,
        answers,
        raw_answer,
    ):
        captured["answer"] = {
            "request_id": request_id,
            "user_id": user_id,
            "answers": answers,
            "raw_answer": raw_answer,
        }
        return EligibilityResultResponse(
            request_id=str(request_id),
            status=RequestStatus.FOLLOW_UP_REQUIRED,
            policy_id="24",
            slug="WLF00000024",
            policy_name="테스트 정책",
            follow_up_questions=[
                {
                    "field_name": "income",
                    "question_text": "소득 구간을 알려주세요.",
                    "priority": 1,
                    "follow_up_id": "1",
                }
            ],
        )

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "answer_eligibility_follow_up",
        fake_answer_eligibility_follow_up,
    )

    app = FastAPI()
    app.include_router(eligibility_router)
    app.dependency_overrides[get_db_session] = fake_db
    app.dependency_overrides[get_current_user] = fake_current_user
    register_exception_handlers(app)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/eligibility/requests/123/answers",
            json={
                "answers": {
                    "region": "seoul",
                    "care_status": "home_care",
                },
                "raw_answer": "서울이고 가정양육 중이에요",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["request_id"] == "123"
    assert body["data"]["status"] == "FOLLOW_UP_REQUIRED"
    assert body["meta"] == {
        "request_id": "123",
        "follow_up_required": True,
    }
    assert captured["answer"] == {
        "request_id": 123,
        "user_id": 7,
        "answers": {
            "region": "seoul",
            "care_status": "home_care",
        },
        "raw_answer": "서울이고 가정양육 중이에요",
    }
    assert captured["committed"] is True


def test_eligibility_result_response_maps_assessment_to_user_response() -> None:
    service = AiRequestLifecycleService()
    request = SimpleNamespace(
        request_id=123,
        request_status=RequestStatus.COMPLETED.value,
        policy_id=24,
        parsed_query_json={"selected_conditions": {"region": "seoul"}},
        merged_condition_json={},
        error_message=None,
    )
    response = service.to_eligibility_result_response(
        request=request,
        policy={
            "policy_code": "WLF00000024",
            "policy_name": "테스트 정책",
        },
        assessment={
            "assessment_status": "LIKELY_MATCH",
            "reason_summary": "지원 가능성이 높습니다.",
            "matched_conditions_json": ["region"],
            "missing_conditions_json": [],
            "conflicting_conditions_json": [],
            "manual_check_points_json": [],
            "evidences": [
                {
                    "chunk_id": 10,
                    "evidence_policy_id": "24",
                    "snippet": "지원 대상 근거",
                    "source_title": "정책 상세",
                    "source_url": "https://example.com",
                    "similarity_score": 0.12,
                    "evidence_role": "TARGET",
                }
            ],
        },
    )

    assert response.user_status == "RECOMMENDABLE"
    assert response.banner_level == "high"
    assert response.criteria[0].status == "ok"
    assert response.evidences[0].evidence_role == "target"
    assert response.input_summary == {"region": "seoul"}


def test_recommendation_polling_result_includes_card_eligibility_fields() -> None:
    service = AiRequestLifecycleService()
    request = SimpleNamespace(
        request_id=321,
        request_status=RequestStatus.COMPLETED.value,
        result_json={
            "results": [
                {
                    "policy_id": "24",
                    "policy_name": "테스트 정책",
                    "summary": "정책 요약",
                    "why_recommended": "자녀 나이 조건과 맞습니다.",
                    "check_before_apply": "소득 기준은 공식 신청 전 확인이 필요합니다.",
                    "user_status": "NEEDS_CONFIRMATION",
                    "assessment_status": "NEEDS_MORE_INFO",
                    "reason_summary": "추가 확인이 필요합니다.",
                    "matched_conditions": ["child_age"],
                    "missing_conditions": ["income"],
                    "manual_check_points": ["공식 신청 전 확인 필요"],
                    "evidence": [],
                }
            ]
        },
        parsed_query_json={},
        error_message=None,
    )

    response = service.to_recommendation_polling_response(request)
    item = response.results[0]

    assert item.policy_id == "24"
    assert item.user_status == "NEEDS_CONFIRMATION"
    assert item.assessment_status == "NEEDS_MORE_INFO"
    assert item.reason_summary == "추가 확인이 필요합니다."
    assert item.matched_conditions == ["child_age"]
    assert item.missing_conditions == ["income"]
    assert item.manual_check_points == ["공식 신청 전 확인 필요"]


def test_answer_eligibility_follow_up_merges_answers_and_keeps_request_id(monkeypatch) -> None:
    captured: dict[str, object] = {}
    request = SimpleNamespace(
        request_id=123,
        request_status=RequestStatus.FOLLOW_UP_REQUIRED.value,
        user_id=7,
        policy_id=24,
        source_type="RECOMMENDATION_RESULT",
        source_ref_id="recommendation-1",
        raw_query="부모급여 가능해?",
        parsed_query_json={
            "selected_conditions": {"childAge": "0"},
            "questions": [{"field_name": "region", "question_text": "지역은?"}],
        },
        merged_condition_json={"childAge": "0"},
        profile_conflict_json=[],
        error_message=None,
    )

    class FakeRepository:
        async def find_by_id(self, db, request_type, request_id):
            return request

        async def update_payload(
            self,
            db,
            request,
            parsed_query_json=None,
            merged_condition_json=None,
            profile_conflict_json=None,
        ):
            captured["payload"] = {
                "parsed_query_json": parsed_query_json,
                "merged_condition_json": merged_condition_json,
                "profile_conflict_json": profile_conflict_json,
            }
            request.parsed_query_json = parsed_query_json
            request.merged_condition_json = merged_condition_json
            request.profile_conflict_json = profile_conflict_json
            return request

        async def update_status(self, db, request, status, error_message=None):
            captured.setdefault("statuses", []).append(status)
            request.request_status = status.value
            request.error_message = error_message
            return request

    async def fake_process(self, db, request_type, request_id):
        captured["process"] = {
            "request_type": request_type,
            "request_id": request_id,
            "selected_conditions": request.parsed_query_json["selected_conditions"],
        }
        request.request_status = RequestStatus.COMPLETED.value
        return self.to_snapshot(request_type, request)

    async def fake_get(self, db, request_id, user_id):
        captured["get_result"] = {"request_id": request_id, "user_id": user_id}
        return EligibilityResultResponse(
            request_id=str(request_id),
            status=RequestStatus.COMPLETED,
            policy_id="24",
            slug="WLF00000024",
            policy_name="테스트 정책",
            user_status="NEEDS_CONFIRMATION",
        )

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "process_condition_request",
        fake_process,
    )
    monkeypatch.setattr(
        AiRequestLifecycleService,
        "get_eligibility_result",
        fake_get,
    )

    service = AiRequestLifecycleService(repository=FakeRepository())

    import asyncio

    response = asyncio.run(
        service.answer_eligibility_follow_up(
            db=SimpleNamespace(),
            request_id=123,
            user_id=7,
            answers={"region": "seoul", "income": "잘 모르겠어요"},
            raw_answer="서울이고 소득은 잘 모르겠어요",
        )
    )

    assert response.request_id == "123"
    assert captured["payload"]["parsed_query_json"]["selected_conditions"] == {
        "childAge": "0",
        "region": "seoul",
        "income": "unknown",
    }
    assert captured["payload"]["merged_condition_json"] == {
        "childAge": "0",
        "region": "seoul",
        "income": "unknown",
    }
    assert captured["statuses"] == [RequestStatus.PROCESSING]
    assert captured["process"] == {
        "request_type": "eligibility",
        "request_id": 123,
        "selected_conditions": {
            "childAge": "0",
            "region": "seoul",
            "income": "unknown",
        },
    }
    assert captured["get_result"] == {"request_id": 123, "user_id": 7}


def test_process_eligibility_request_saves_policy_assessment(monkeypatch) -> None:
    captured: dict[str, object] = {}
    request = SimpleNamespace(
        request_id=123,
        request_status=RequestStatus.PROCESSING.value,
        user_id=7,
        policy_id=24,
        source_type="POLICY_DETAIL",
        source_ref_id="WLF00000024",
        raw_query=None,
        parsed_query_json={"selected_conditions": {"region": "seoul"}},
        merged_condition_json={},
        profile_conflict_json=[],
        error_message=None,
    )

    class FakeRepository:
        async def find_by_id(self, db, request_type, request_id):
            captured["find"] = {
                "request_type": request_type,
                "request_id": request_id,
            }
            return request

        async def update_payload(
            self,
            db,
            request,
            parsed_query_json=None,
            merged_condition_json=None,
            profile_conflict_json=None,
        ):
            captured["payload"] = {
                "parsed_query_json": parsed_query_json,
                "merged_condition_json": merged_condition_json,
                "profile_conflict_json": profile_conflict_json,
            }
            request.parsed_query_json = parsed_query_json
            request.merged_condition_json = merged_condition_json
            request.profile_conflict_json = profile_conflict_json
            return request

        async def update_status(self, db, request, status, error_message=None):
            captured["status"] = status
            request.request_status = status.value
            request.error_message = error_message
            return request

    class FakeConditionAgent:
        async def analyze(self, condition_input):
            captured["condition_input"] = condition_input
            return ConditionResult(
                parsed_query_json={
                    "selected_conditions": {"region": "seoul"},
                },
                merged_condition_json={
                    "region": "seoul",
                    "matched_conditions": ["region"],
                },
            )

    class FakeAssessmentRepository:
        async def find_policy_evidence_chunks(self, db, policy_id, limit=8):
            captured["evidence_chunks"] = {
                "policy_id": policy_id,
                "limit": limit,
            }
            return []

    async def fake_chunk_searcher(**kwargs):
        captured["rag_search"] = kwargs
        return []

    class FakeConnection:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def connection(self):
            return FakeConnection()

    async def fake_save_assessment(
        conn,
        result,
        assessment_type,
        recommendation_request_id=None,
        eligibility_request_id=None,
        confidence_score=None,
        selected_for_result=False,
    ):
        captured["saved_assessment"] = {
            "conn": conn,
            "policy_id": result.policy_id,
            "assessment_status": result.assessment_status.value,
            "assessment_type": assessment_type,
            "eligibility_request_id": eligibility_request_id,
            "matched_conditions": result.matched_conditions,
        }
        return 999

    monkeypatch.setattr(
        "app.services.ai_request_lifecycle_service.psycopg_pool",
        FakePool(),
    )
    monkeypatch.setattr(
        "app.services.ai_request_lifecycle_service.PolicyAssessmentRepository.save_assessment",
        fake_save_assessment,
    )

    async def fake_profile_snapshot(self, db, user_id):
        return None

    monkeypatch.setattr(
        AiRequestLifecycleService,
        "_profile_snapshot",
        fake_profile_snapshot,
    )

    service = AiRequestLifecycleService(
        repository=FakeRepository(),
        assessment_repository=FakeAssessmentRepository(),
        condition_agent=FakeConditionAgent(),
        policy_chunk_searcher=fake_chunk_searcher,
    )

    import asyncio

    asyncio.run(
        service.process_condition_request(
            db=SimpleNamespace(),
            request_type="eligibility",
            request_id=123,
        )
    )

    assert captured["rag_search"]["policy_ids"] == [24]
    assert "충족 조건: region" in captured["rag_search"]["query"]
    assert captured["evidence_chunks"] == {
        "policy_id": 24,
        "limit": 8,
    }
    assert captured["status"] == RequestStatus.COMPLETED
    assert captured["saved_assessment"] == {
        "conn": "conn",
        "policy_id": 24,
        "assessment_status": "LIKELY_MATCH",
        "assessment_type": "eligibility_detail",
        "eligibility_request_id": 123,
        "matched_conditions": ["region"],
    }


def test_recommendation_result_eligibility_skips_saved_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}
    request = SimpleNamespace(
        request_id=124,
        request_status=RequestStatus.PROCESSING.value,
        user_id=7,
        policy_id=24,
        source_type="RECOMMENDATION_RESULT",
        source_ref_id="recommendation-1",
        raw_query=None,
        parsed_query_json={
            "selected_conditions": {
                "region": "seoul",
                "stage": "newborn",
                "income": "mid1",
            }
        },
        merged_condition_json={},
        profile_conflict_json=[],
        error_message=None,
    )

    class FakeRepository:
        async def find_by_id(self, db, request_type, request_id):
            return request

        async def update_payload(
            self,
            db,
            request,
            parsed_query_json=None,
            merged_condition_json=None,
            profile_conflict_json=None,
        ):
            captured["payload"] = {
                "merged_condition_json": merged_condition_json,
                "profile_conflict_json": profile_conflict_json,
            }
            request.parsed_query_json = parsed_query_json
            request.merged_condition_json = merged_condition_json
            request.profile_conflict_json = profile_conflict_json
            return request

        async def update_status(self, db, request, status, error_message=None):
            captured["status"] = status
            request.request_status = status.value
            request.error_message = error_message
            return request

    class FakeConditionAgent:
        async def analyze(self, condition_input):
            captured["condition_profile_snapshot"] = condition_input.profile_snapshot
            return ConditionResult(
                parsed_query_json={
                    "selected_conditions": condition_input.selected_conditions,
                },
                merged_condition_json={
                    **condition_input.selected_conditions,
                    "matched_conditions": ["region"],
                },
                profile_conflicts=[],
            )

    class FakeAssessmentRepository:
        async def find_policy_evidence_chunks(self, db, policy_id, limit=8):
            return []

    async def fake_chunk_searcher(**kwargs):
        captured["rag_search"] = kwargs
        return []

    class FakeConnection:
        async def __aenter__(self):
            return "conn"

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def connection(self):
            return FakeConnection()

    async def fake_save_assessment(
        conn,
        result,
        assessment_type,
        recommendation_request_id=None,
        eligibility_request_id=None,
        confidence_score=None,
        selected_for_result=False,
    ):
        captured["saved_status"] = result.assessment_status.value
        captured["saved_conflicts"] = result.conflicting_conditions
        return 999

    async def fake_profile_snapshot(self, db, user_id):
        captured["profile_snapshot_called"] = True
        return {
            "region": "busan",
            "stage": "teen",
            "income": "high",
        }

    monkeypatch.setattr(
        "app.services.ai_request_lifecycle_service.psycopg_pool",
        FakePool(),
    )
    monkeypatch.setattr(
        "app.services.ai_request_lifecycle_service.PolicyAssessmentRepository.save_assessment",
        fake_save_assessment,
    )
    monkeypatch.setattr(
        AiRequestLifecycleService,
        "_profile_snapshot",
        fake_profile_snapshot,
    )

    service = AiRequestLifecycleService(
        repository=FakeRepository(),
        assessment_repository=FakeAssessmentRepository(),
        condition_agent=FakeConditionAgent(),
        policy_chunk_searcher=fake_chunk_searcher,
    )

    import asyncio

    asyncio.run(
        service.process_condition_request(
            db=SimpleNamespace(),
            request_type="eligibility",
            request_id=124,
        )
    )

    assert captured.get("profile_snapshot_called") is None
    assert captured["condition_profile_snapshot"] is None
    assert captured["rag_search"]["policy_ids"] == [24]
    assert captured["payload"] == {
        "merged_condition_json": {
            "region": "seoul",
            "stage": "newborn",
            "income": "mid1",
            "matched_conditions": ["region"],
        },
        "profile_conflict_json": [],
    }
    assert captured["status"] == RequestStatus.COMPLETED
    assert captured["saved_status"] == "LIKELY_MATCH"
    assert captured["saved_conflicts"] == []


def test_eligibility_evidence_uses_condition_based_rag_before_fallback() -> None:
    captured: dict[str, object] = {}

    class FakeAssessmentRepository:
        async def find_policy_evidence_chunks(self, db, policy_id, limit=8):
            captured["fallback_called"] = True
            return []

    async def fake_chunk_searcher(**kwargs):
        captured["rag_search"] = kwargs
        return [
            EvidenceChunk(
                chunk_id=9,
                policy_id=24,
                snippet="서울 거주 임산부 대상 지원 근거",
                source_title="테스트 정책 - 지원 대상",
                source_url="",
                score=0.91,
                evidence_role="TARGET",
            )
        ]

    service = AiRequestLifecycleService(
        assessment_repository=FakeAssessmentRepository(),
        policy_chunk_searcher=fake_chunk_searcher,
    )
    request = SimpleNamespace(
        request_id=123,
        raw_query=None,
        parsed_query_json={"selected_conditions": {"region": "seoul", "stage": "pregnant"}},
    )

    import asyncio

    chunks = asyncio.run(
        service._find_eligibility_evidence_chunks(
            db=SimpleNamespace(),
            policy_id=24,
            condition={
                "region": "seoul",
                "stage": "pregnant",
                "matched_conditions": ["서울 거주"],
                "manual_check_points": ["임신 여부 확인"],
            },
            request=request,
        )
    )

    assert captured.get("fallback_called") is None
    assert captured["rag_search"]["policy_ids"] == [24]
    assert "사용자 입력 조건: region=seoul, stage=pregnant" in captured["rag_search"]["query"]
    assert "충족 조건: 서울 거주" in captured["rag_search"]["query"]
    assert chunks[0].chunk_id == 9

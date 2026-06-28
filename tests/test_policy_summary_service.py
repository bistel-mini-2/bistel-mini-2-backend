import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from app.ai.agents.policy_summary_agent import LangChainPolicySummaryGenerator
from app.common.exceptions import AppException
from app.repositories.policy_repository import PolicyRepository
from app.repositories.policy_summary_repository import PolicySummaryRepository
from app.schemas.ai_contract import RequestStatus
from app.services.policy_summary_service import (
    POLICY_SUMMARY_STALE_AFTER_MINUTES,
    PolicySummaryService,
)


def test_get_or_start_summary_returns_loading_and_start_id(monkeypatch) -> None:
    db = object()
    repository = AsyncMock()
    repository.create_processing_if_absent.return_value = (
        {
            "summary_id": 7,
            "policy_id": 1,
            "request_status": RequestStatus.PROCESSING.value,
            "summary": None,
            "evidence_json": None,
        },
        True,
    )
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value={"policy_id": 1}),
    )

    response, start_id = asyncio.run(
        PolicySummaryService(repository=repository, graph=AsyncMock()).get_or_start_summary(
            db,  # type: ignore[arg-type]
            policy_slug=" WLF00000024 ",
        )
    )

    assert response.status == "loading"
    assert response.summary is None
    assert response.evidence == []
    assert start_id == 7
    repository.create_processing_if_absent.assert_awaited_once_with(
        db,
        1,
        force_refresh=False,
        stale_after_minutes=POLICY_SUMMARY_STALE_AFTER_MINUTES,
    )


def test_get_or_start_summary_returns_cached_done_without_start(monkeypatch) -> None:
    db = object()
    repository = AsyncMock()
    repository.create_processing_if_absent.return_value = (
        {
            "summary_id": 7,
            "policy_id": 1,
            "request_status": RequestStatus.COMPLETED.value,
            "summary": "line 1\nline 2\nline 3",
            "evidence_json": ["target evidence", "benefit evidence"],
        },
        False,
    )
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value={"policy_id": 1}),
    )

    response, start_id = asyncio.run(
        PolicySummaryService(repository=repository, graph=AsyncMock()).get_or_start_summary(
            db,  # type: ignore[arg-type]
            policy_slug="WLF00000024",
        )
    )

    assert response.status == "done"
    assert response.summary == "line 1\nline 2\nline 3"
    assert response.evidence == ["target evidence", "benefit evidence"]
    assert start_id is None


def test_get_or_start_summary_can_force_refresh(monkeypatch) -> None:
    db = object()
    repository = AsyncMock()
    repository.create_processing_if_absent.return_value = (
        {
            "summary_id": 7,
            "policy_id": 1,
            "request_status": RequestStatus.PROCESSING.value,
            "summary": None,
            "evidence_json": None,
        },
        True,
    )
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value={"policy_id": 1}),
    )

    response, start_id = asyncio.run(
        PolicySummaryService(
            repository=repository,
            graph=AsyncMock(),
        ).get_or_start_summary(
            db,  # type: ignore[arg-type]
            policy_slug="WLF00000024",
            force_refresh=True,
        )
    )

    assert response.status == "loading"
    assert start_id == 7
    repository.create_processing_if_absent.assert_awaited_once_with(
        db,
        1,
        force_refresh=True,
        stale_after_minutes=POLICY_SUMMARY_STALE_AFTER_MINUTES,
    )


def test_get_or_start_summary_maps_failed_cache_to_error(monkeypatch) -> None:
    db = object()
    repository = AsyncMock()
    repository.create_processing_if_absent.return_value = (
        {
            "summary_id": 7,
            "policy_id": 1,
            "request_status": RequestStatus.FAILED.value,
            "summary": None,
            "evidence_json": None,
        },
        False,
    )
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value={"policy_id": 1}),
    )

    response, start_id = asyncio.run(
        PolicySummaryService(repository=repository, graph=AsyncMock()).get_or_start_summary(
            db,  # type: ignore[arg-type]
            policy_slug="WLF00000024",
        )
    )

    assert response.status == "error"
    assert response.summary is None
    assert response.evidence == []
    assert start_id is None


def test_get_or_start_summary_rejects_unknown_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        PolicyRepository,
        "find_policy_detail",
        AsyncMock(return_value=None),
    )

    with pytest.raises(AppException) as exc_info:
        asyncio.run(
            PolicySummaryService(
                repository=AsyncMock(),
                graph=AsyncMock(),
            ).get_or_start_summary(
                object(),  # type: ignore[arg-type]
                policy_slug="UNKNOWN",
            )
        )

    assert exc_info.value.status_code == 404


def test_process_summary_saves_graph_result() -> None:
    db = object()
    repository = AsyncMock()
    repository.find_generation_context.return_value = {
        "summary_id": 7,
        "request_status": RequestStatus.PROCESSING.value,
        "policy_id": 1,
        "name": "policy",
    }
    graph = AsyncMock()
    graph.run.return_value = {
        "summary": "generated summary",
        "evidence": ["evidence 1", "evidence 2"],
    }

    asyncio.run(
        PolicySummaryService(repository=repository, graph=graph).process_summary(
            db,  # type: ignore[arg-type]
            summary_id=7,
        )
    )

    graph.run.assert_awaited_once_with(repository.find_generation_context.return_value)
    repository.mark_completed.assert_awaited_once_with(
        db,
        summary_id=7,
        summary="generated summary",
        evidence=["evidence 1", "evidence 2"],
    )
    repository.mark_failed.assert_not_awaited()


def test_summary_cache_detects_condition_profile_change() -> None:
    previous_updated_at = datetime(2026, 1, 1, 0, 0, 0)
    current_updated_at = datetime(2026, 1, 2, 0, 0, 0)

    assert PolicySummaryRepository._profile_changed(
        {
            "condition_profile_id": 10,
            "condition_profile_updated_at": previous_updated_at,
        },
        {
            "condition_profile_id": 10,
            "updated_at": current_updated_at,
        },
    )
    assert PolicySummaryRepository._profile_changed(
        {
            "condition_profile_id": 10,
            "condition_profile_updated_at": current_updated_at,
        },
        {
            "condition_profile_id": 11,
            "updated_at": current_updated_at,
        },
    )
    assert not PolicySummaryRepository._profile_changed(
        {
            "condition_profile_id": 10,
            "condition_profile_updated_at": current_updated_at,
        },
        {
            "condition_profile_id": 10,
            "updated_at": current_updated_at,
        },
    )


def test_summary_fallback_uses_condition_profile_source_text_as_evidence() -> None:
    generator = LangChainPolicySummaryGenerator()

    result = generator._fallback(
        {
            "name": "테스트 정책",
            "condition_profile_target_summary": "정제된 대상 요약",
            "condition_profile_source_text": "원본 선정기준: 만 2세 미만 아동",
            "target_description": "기존 상세 대상 설명",
            "benefit_description": "월 10만원 지원",
        },
        [],
    )
    prompt = generator._prompt(
        {
            "name": "테스트 정책",
            "condition_profile_json": {
                "condition_tree": {
                    "field": "child_age",
                    "operator": "LT",
                    "value": 2,
                }
            },
            "condition_profile_source_text": "원본 선정기준: 만 2세 미만 아동",
            "condition_profile_source_fields": ["raw_selection_criteria"],
            "target_description": "기존 상세 대상 설명",
        },
        [],
    )

    assert result.evidence[0] == "원본 선정기준: 만 2세 미만 아동"
    assert "policy_condition_profile" in prompt
    assert "condition_json" in prompt
    assert "원본 선정기준: 만 2세 미만 아동" in prompt

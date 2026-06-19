import pytest

from app.common.ai_status import AssessmentStatus, UserStatus
from app.schemas.ai_contract import AssessmentInput, EvidenceChunk
from app.schemas.policy_rag_schema import PolicyRagSearchResult
from app.services.policy_assessment_service import (
    ASSESSMENT_TYPE_ELIGIBILITY,
    ASSESSMENT_TYPE_RECOMMENDATION,
    PolicyAssessmentService,
)


@pytest.fixture
def service() -> PolicyAssessmentService:
    return PolicyAssessmentService()


def make_input(condition: dict, policy_id: int = 1) -> AssessmentInput:
    return AssessmentInput(
        policy_id=policy_id,
        merged_condition_json=condition,
        evidence_chunks=[
            EvidenceChunk(
                chunk_id=10,
                policy_id=policy_id,
                snippet="지원 대상 근거",
                source_title="정책 상세",
                source_url="https://example.com",
                score=0.1,
                evidence_role="target",
            )
        ],
    )


def test_likely_match(service: PolicyAssessmentService):
    result = service.assess_policy(
        make_input({"matched_conditions": ["region", "income"]}),
        policy_id=1,
    )

    assert result.assessment_status == AssessmentStatus.LIKELY_MATCH
    assert result.user_status == UserStatus.RECOMMENDABLE
    assert result.matched_conditions == ["region", "income"]
    assert result.evidences[0].evidence_role == "target"


def test_needs_more_info_with_one_missing_condition(
    service: PolicyAssessmentService,
):
    result = service.assess_policy(
        make_input({"missing_conditions": ["income"]}),
        policy_id=1,
    )

    assert result.assessment_status == AssessmentStatus.NEEDS_MORE_INFO
    assert result.user_status == UserStatus.NEEDS_CONFIRMATION
    assert result.missing_conditions == ["income"]


def test_not_match_with_failed_condition(service: PolicyAssessmentService):
    result = service.assess_policy(
        make_input({"failed_conditions": ["region"]}),
        policy_id=1,
    )

    assert result.assessment_status == AssessmentStatus.NOT_MATCH
    assert result.user_status == UserStatus.DIFFICULT_TO_RECOMMEND


def test_insufficient_profile_with_two_missing_conditions(
    service: PolicyAssessmentService,
):
    result = service.assess_policy(
        make_input({"missing_conditions": ["income", "region"]}),
        policy_id=1,
    )

    assert result.assessment_status == AssessmentStatus.INSUFFICIENT_PROFILE
    assert result.user_status == UserStatus.NEEDS_CONFIRMATION


def test_conflicting_profile_when_rule_result_diverges(
    service: PolicyAssessmentService,
):
    result = service.assess_policy(
        make_input(
            {
                "profile_conflicts": [
                    {
                        "field_name": "region",
                        "changes_rule_filter_result": True,
                    }
                ],
                "normalized_candidates": [
                    {"region": "seoul"},
                    {"region": "busan"},
                ],
            }
        ),
        policy_id=1,
    )

    assert result.assessment_status == AssessmentStatus.CONFLICTING_PROFILE
    assert result.user_status == UserStatus.NEEDS_CONFIRMATION
    assert result.conflicting_conditions == ["region"]


def test_assessment_type_branches(service: PolicyAssessmentService):
    assessment_input = make_input({"matched_conditions": ["income"]}, policy_id=7)

    recommendation_result = service.assess(
        assessment_input,
        assessment_type=ASSESSMENT_TYPE_RECOMMENDATION,
    )
    eligibility_result = service.assess(
        assessment_input,
        assessment_type=ASSESSMENT_TYPE_ELIGIBILITY,
    )

    assert recommendation_result[0].policy_id == 7
    assert eligibility_result[0].policy_id == 7


def test_rejects_unknown_assessment_type(service: PolicyAssessmentService):
    with pytest.raises(ValueError):
        service.assess(make_input({}), assessment_type="unknown")


def test_build_evidence_chunks_maps_section_to_evidence_role(
    service: PolicyAssessmentService,
):
    evidences = service.build_evidence_chunks(
        [
            PolicyRagSearchResult(
                chunk_id=101,
                document_id=10,
                policy_id=1,
                policy_code="P1",
                policy_name="테스트 정책",
                section="지원 대상",
                source_type="POLICY_DETAIL",
                source_url="https://example.com/policy",
                chunk_text="지원 대상 근거",
                distance=0.2,
            )
        ]
    )

    assert evidences[0].evidence_role == "target"
    assert evidences[0].source_title == "테스트 정책 - 지원 대상"

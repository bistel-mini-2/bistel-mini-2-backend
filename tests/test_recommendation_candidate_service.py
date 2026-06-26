from app.services.recommendation_candidate_service import (
    RecommendationCandidateService,
)


def test_policy_text_includes_condition_profile_source_text() -> None:
    service = RecommendationCandidateService()
    row = {
        "policy_name": "test policy",
        "main_category": None,
        "sub_category": None,
        "provider_name": None,
        "region_scope": None,
        "region_code": None,
        "benefit_type": None,
        "easy_summary": None,
        "target_description": "legacy target",
        "condition_profile_target_summary": "profile target",
        "condition_profile_source_text": "source eligibility text",
        "benefit_description": None,
        "application_method": None,
        "caution": None,
        "tags": [],
    }

    text = service._policy_text(row)

    assert "legacy target" in text
    assert "profile target" in text
    assert "source eligibility text" in text


def test_target_text_prefers_combined_profile_target_context() -> None:
    service = RecommendationCandidateService()
    row = {
        "target_description": "legacy target",
        "condition_profile_target_summary": "profile target",
        "condition_profile_source_text": "source eligibility text",
    }

    text = service._target_text(row)

    assert text == "legacy target profile target source eligibility text"

from app.ai.agents.condition_agent import ConditionAgent


def test_missing_region_does_not_create_follow_up_candidate() -> None:
    agent = ConditionAgent()

    issues = agent._missing_issues(
        {
            "stage": "pregnant",
            "income": "mid1",
        }
    )
    candidates = agent._follow_up_candidates(issues)

    assert [issue.field_name for issue in issues] == []
    assert candidates == []


def test_missing_income_creates_follow_up_candidate() -> None:
    agent = ConditionAgent()

    issues = agent._missing_issues(
        {
            "stage": "pregnant",
        }
    )
    candidates = agent._follow_up_candidates(issues)

    assert [issue.field_name for issue in issues] == ["income"]
    assert candidates[0].field_name == "income"
    assert candidates[0].question_text == "대략적인 소득 구간을 알려주세요."
    assert candidates[0].priority == 3


def test_missing_stage_or_child_age_creates_follow_up_candidate() -> None:
    agent = ConditionAgent()

    issues = agent._missing_issues(
        {
            "income": "mid1",
        }
    )
    candidates = agent._follow_up_candidates(issues)

    assert [issue.field_name for issue in issues] == ["stage"]
    assert candidates[0].field_name == "stage"
    assert candidates[0].question_text == "임신/출산/양육 단계나 자녀 나이를 알려주세요."

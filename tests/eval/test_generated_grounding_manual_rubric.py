import json

import pytest

from tests.eval.generated_grounding_manual_rubric import (
    MANUAL_REVIEW_SAMPLES,
    build_manual_grounding_report,
    write_json,
    write_markdown,
)


def test_manual_grounding_report_keeps_generation_separate_from_retrieval():
    report = build_manual_grounding_report()

    assert report["evaluation_name"] == "generated_answer_grounding_manual_rubric"
    assert report["evaluation_status"] == "manual_sample_review"
    assert report["not_evaluated"] == "retrieval_correctness_or_live_api_faithfulness"
    assert report["source_context"]["runtime_calls"] == {
        "db": False,
        "retrieval_api": False,
        "llm": False,
    }
    assert report["metrics"]["sample_count"] == 10
    assert report["metrics"]["checkable_claim_count"] == 9
    assert report["metrics"]["claim_grounding_rate_pct"] == 77.8
    assert report["metrics"]["critical_error_count"] == 1
    assert report["counts"]["by_issue_type"]["generic_only"] == 1
    assert any(
        "live API faithfulness는 아니다" in claim
        for claim in report["portfolio_safe_claims"]
    )


def test_manual_grounding_report_validates_exact_sample_count():
    with pytest.raises(ValueError, match="exactly 10 samples"):
        build_manual_grounding_report(MANUAL_REVIEW_SAMPLES[:9])


def test_manual_grounding_report_rejects_unsupported_without_issue():
    samples = [dict(sample) for sample in MANUAL_REVIEW_SAMPLES]
    samples[-1] = {
        **samples[-1],
        "grounding_status": "unsupported",
        "issue_type": "none",
    }

    with pytest.raises(ValueError, match="unsupported claims need an issue_type"):
        build_manual_grounding_report(samples)


def test_manual_grounding_artifacts_are_writable(tmp_path):
    report = build_manual_grounding_report()
    output = tmp_path / "generated_grounding_manual_rubric.json"
    markdown = tmp_path / "generated_grounding_manual_rubric.md"

    write_json(output, report)
    write_markdown(markdown, report)

    loaded = json.loads(output.read_text(encoding="utf-8"))
    text = markdown.read_text(encoding="utf-8")

    assert loaded["metrics"]["sample_count"] == 10
    assert "# Generated Grounding Manual Rubric" in text
    assert "live API faithfulness 주장이 아니다" in text

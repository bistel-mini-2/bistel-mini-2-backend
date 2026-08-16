import json

import pytest

from tests.eval.generated_grounding_live_capture import build_capture_report
from tests.eval.generated_grounding_live_review import (
    build_live_review,
    write_json,
    write_markdown,
)


def test_live_review_scores_completed_capture_claims():
    review = build_live_review(_capture_fixture())

    assert review["evaluation_name"] == "generated_answer_grounding_live_manual_review"
    assert review["evaluation_status"] == "completed_manual_review_on_live_capture"
    assert review["capture_preconditions"]["all_completed"] is True
    assert review["capture_preconditions"]["llm_or_fallback_generator"] is True
    assert review["metrics"]["policy_count"] == 10
    assert review["metrics"]["claim_count"] == 62
    assert review["metrics"]["summary_claim_count"] == 32
    assert review["metrics"]["evidence_claim_count"] == 30
    assert review["metrics"]["claim_grounding_rate_pct"] == 100.0
    assert review["metrics"]["unsupported_claim_count"] == 0
    assert review["metrics"]["critical_error_count"] == 0
    assert review["metrics"]["display_quality_issue_count"] == 3
    assert review["counts"]["by_display_issue_type"]["malformed_text"] == 2
    assert review["counts"]["by_display_issue_type"]["internal_marker"] == 1


def test_live_review_rejects_incomplete_capture():
    capture = _capture_fixture()
    capture["metrics"]["all_completed"] = False

    with pytest.raises(ValueError, match="live capture must be completed"):
        build_live_review(capture)


def test_live_review_artifacts_are_writable(tmp_path):
    review = build_live_review(_capture_fixture())
    output = tmp_path / "live_review.json"
    report = tmp_path / "live_review.md"

    write_json(output, review)
    write_markdown(report, review)

    loaded = json.loads(output.read_text(encoding="utf-8"))
    markdown = report.read_text(encoding="utf-8")

    assert loaded["metrics"]["claim_grounding_rate_pct"] == 100.0
    assert "# Generated Grounding Live Review" in markdown
    assert "Display Quality Issues" in markdown


def _capture_fixture() -> dict:
    records = []
    policy_ids = [237, 238, 240, 242, 246, 247, 251, 255, 289, 292]
    for policy_id in policy_ids:
        summary = (
            "첫 번째 근거 문장입니다. 두 번째 근거 문장입니다. 세 번째 근거 문장입니다."
        )
        if policy_id in {237, 255}:
            summary += "\n대상을 지원합니이 주요 지원 대상이에요."
        evidence = [
            "첫 번째 근거입니다.",
            "두 번째 근거입니다.",
            "세 번째 근거입니다.",
        ]
        if policy_id == 246:
            evidence[-1] = "application_status: OFFLINE_ONLY, application_period_text: 수시"
        records.append(
            {
                "policy_id": policy_id,
                "policy_slug": f"WLF{policy_id}",
                "policy_name": f"정책 {policy_id}",
                "started_status": "loading",
                "start_summary_id": policy_id,
                "force_refresh": True,
                "elapsed_ms": 100.0,
                "process_error": None,
                "cache_status": "COMPLETED",
                "cache_error_message": None,
                "api_response_model": {
                    "status": "done",
                    "summary": summary,
                    "evidence": evidence,
                },
                "summary_line_count": 4 if policy_id in {237, 255} else 3,
                "evidence_count": 3,
                "contains_internal_marker": policy_id == 246,
                "requires_manual_grounding_review": True,
            }
        )
    return build_capture_report(
        records,
        started_at="2026-08-16T00:00:00+0900",
    )

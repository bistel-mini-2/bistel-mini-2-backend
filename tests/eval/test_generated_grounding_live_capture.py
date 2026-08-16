import json

from tests.eval.generated_grounding_live_capture import (
    build_capture_report,
    write_json,
    write_markdown,
)


def test_live_capture_report_marks_manual_review_boundary():
    payload = build_capture_report(
        [
            {
                "policy_id": 237,
                "policy_slug": "WLF00006317",
                "policy_name": "발달장애인 자기주도 재직자 훈련",
                "started_status": "loading",
                "start_summary_id": 1,
                "force_refresh": True,
                "elapsed_ms": 1200.0,
                "process_error": None,
                "cache_status": "COMPLETED",
                "cache_error_message": None,
                "api_response_model": {
                    "status": "done",
                    "summary": "첫 줄\n둘째 줄\n셋째 줄",
                    "evidence": ["근거 1", "근거 2"],
                },
                "summary_line_count": 3,
                "evidence_count": 2,
                "contains_internal_marker": False,
                "requires_manual_grounding_review": True,
            }
        ],
        started_at="2026-08-16T00:00:00+0900",
    )

    assert payload["evaluation_name"] == "generated_grounding_live_api_capture"
    assert payload["metrics"]["requested_policy_count"] == 1
    assert payload["metrics"]["completed_response_count"] == 1
    assert payload["metrics"]["all_completed"] is True
    assert payload["metrics"]["all_clean_display_text"] is True
    assert "does not prove" not in payload["grounding_boundary"]["what_this_proves"].lower()
    assert "not yet" in payload["grounding_boundary"]["what_this_does_not_prove"]


def test_live_capture_artifacts_are_writable(tmp_path):
    payload = build_capture_report(
        [
            {
                "policy_id": 1,
                "policy_slug": "P1",
                "policy_name": "정책",
                "started_status": "loading",
                "start_summary_id": 1,
                "force_refresh": True,
                "elapsed_ms": 10.0,
                "process_error": None,
                "cache_status": "COMPLETED",
                "cache_error_message": None,
                "api_response_model": {
                    "status": "done",
                    "summary": "요약",
                    "evidence": ["근거"],
                },
                "summary_line_count": 1,
                "evidence_count": 1,
                "contains_internal_marker": False,
                "requires_manual_grounding_review": True,
            }
        ],
        started_at="2026-08-16T00:00:00+0900",
    )
    output = tmp_path / "capture.json"
    report = tmp_path / "capture.md"

    write_json(output, payload)
    write_markdown(report, payload)

    assert json.loads(output.read_text(encoding="utf-8"))["metrics"]["all_completed"]
    assert "Generated Grounding Live API Capture" in report.read_text(encoding="utf-8")

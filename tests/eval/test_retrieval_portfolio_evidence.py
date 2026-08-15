import json
from pathlib import Path

from tests.eval.retrieval_portfolio_evidence import (
    CASE_TYPE_CRITERIA,
    build_all,
    build_taxonomy,
    load_cases,
)


def test_taxonomy_classifies_all_goldset_cases_once():
    cases = load_cases()
    taxonomy = build_taxonomy(cases)

    assert taxonomy["case_count"] == 50
    assert len(taxonomy["cases"]) == 50
    assert {
        case["type"]
        for case in taxonomy["cases"]
    } <= set(CASE_TYPE_CRITERIA)
    assert sum(taxonomy["counts"].values()) == 50
    assert taxonomy["counts"]["direct_policy_name"] == 0


def test_portfolio_evidence_artifacts_keep_faithfulness_out_of_scope():
    artifacts = build_all()

    assert (
        artifacts["evidence_quality"]["evaluation_name"]
        == "retrieved_evidence_correctness"
    )
    assert (
        artifacts["evidence_quality"]["not_evaluated"]
        == "generated_answer_faithfulness"
    )
    assert (
        artifacts["strategy_decision_summary"]["decision_candidate"]
        == "adaptive"
    )
    assert (
        artifacts["latency_repeated"]["execution_mode"]
        == "fixture_from_existing_single_run_benchmarks"
    )
    assert artifacts["latency_repeated"]["actual_repeated_run_available"] is False


def test_generated_artifacts_are_json_serializable(tmp_path):
    artifacts = build_all()
    output = tmp_path / "artifacts.json"

    output.write_text(
        json.dumps(artifacts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert json.loads(output.read_text(encoding="utf-8"))["taxonomy"]["case_count"] == 50

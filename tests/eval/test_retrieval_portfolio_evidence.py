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
    artifacts = build_all(
        repeated_broad_path=Path("missing-broad-repeated.json"),
        repeated_scoped_path=Path("missing-scoped-repeated.json"),
    )

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


def test_portfolio_evidence_uses_actual_repeated_latency_when_available(tmp_path):
    broad = tmp_path / "broad.json"
    scoped = tmp_path / "scoped.json"
    broad.write_text(
        json.dumps(
            _repeated_payload(
                scope_to_expected_policy=False,
                strategies=("vector", "hybrid", "adaptive"),
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    scoped.write_text(
        json.dumps(
            _repeated_payload(
                scope_to_expected_policy=True,
                strategies=("vector", "adaptive"),
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    artifacts = build_all(
        repeated_broad_path=broad,
        repeated_scoped_path=scoped,
    )

    assert artifacts["latency_repeated"]["execution_mode"] == "actual_repeated_benchmark"
    assert artifacts["latency_repeated"]["actual_repeated_run_available"] is True
    assert artifacts["latency_repeated"]["repeat_run_count"] == 5
    assert (
        artifacts["latency_repeated"]["summaries"][0]["strategies"][0][
            "median_latency_p50_ms"
        ]
        == 100.0
    )
    assert (
        artifacts["strategy_decision_summary"]["repeated_latency_tradeoff"][
            "actual_repeated_run_available"
        ]
        is True
    )
    assert (
        artifacts["strategy_decision_summary"]["benchmark_execution_improvements"][
            "observed_embedding_call_scale"
        ]["total_estimated_embedding_calls"]
        == 1500
    )


def test_generated_artifacts_are_json_serializable(tmp_path):
    artifacts = build_all()
    output = tmp_path / "artifacts.json"

    output.write_text(
        json.dumps(artifacts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    assert json.loads(output.read_text(encoding="utf-8"))["taxonomy"]["case_count"] == 50


def _repeated_payload(
    *,
    scope_to_expected_policy: bool,
    strategies: tuple[str, ...],
) -> dict:
    return {
        "execution_mode": "actual_repeated_benchmark",
        "dataset": "tests/eval/retrieval_cases.jsonl",
        "top_k": 5,
        "case_count": 50,
        "scope_to_expected_policy": scope_to_expected_policy,
        "case_timeout_seconds": 15.0,
        "run_count": 5,
        "warmup_run_count": 1,
        "aggregation_note": "Repeated latency excludes warm-up runs.",
        "strategies": [
            {
                "strategy": strategy,
                "run_count": 5,
                "case_count": 50,
                "policy_hit_at_k_pct_values": [100.0] * 5,
                "section_hit_at_k_pct_values": [98.0] * 5,
                "mrr_values": [1.0] * 5,
                "error_rate_pct_values": [0.0] * 5,
                "latency_p50_ms_values": [90.0, 100.0, 110.0, 100.0, 100.0],
                "latency_p95_ms_values": [190.0, 200.0, 210.0, 200.0, 200.0],
                "latency_p50_ms": 100.0,
                "latency_p95_ms": 200.0,
                "fallback_count_values": [None] * 5,
                "fallback_rate_pct_values": [None] * 5,
            }
            for strategy in strategies
        ],
    }

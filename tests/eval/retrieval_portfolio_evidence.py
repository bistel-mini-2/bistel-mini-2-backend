"""Build portfolio-facing retrieval evidence artifacts from benchmark outputs.

The artifacts in this module summarize verified retrieval evidence only. They
do not evaluate generated-answer faithfulness.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_CASES_PATH = Path("tests/eval/retrieval_cases.jsonl")
DEFAULT_BROAD_PATH = Path("output/retrieval_benchmark_50.json")
DEFAULT_SCOPED_PATH = Path("output/retrieval_benchmark_scoped_50.json")
DEFAULT_REPEATED_BROAD_PATH = Path("output/retrieval_benchmark_repeated_broad_5.json")
DEFAULT_REPEATED_SCOPED_PATH = Path("output/retrieval_benchmark_repeated_scoped_5.json")

CASE_TYPE_CRITERIA = {
    "direct_policy_name": (
        "Question directly names the target policy. The current goldset has "
        "0 such cases by design."
    ),
    "condition_missing": (
        "Question asks about a missing deadline, exception, caveat, or "
        "eligibility constraint that could block an otherwise relevant answer."
    ),
    "similar_policy": (
        "Question is in a crowded domain where a nearby policy can look like "
        "the answer unless the retriever finds the right policy and section."
    ),
    "ambiguous_question": (
        "Question has reviewed multi-answer labels or wording that can point "
        "to more than one acceptable policy."
    ),
    "eligibility_or_rule": (
        "Question focuses on target eligibility, rule structure, or support "
        "conditions rather than a simple benefit lookup."
    ),
    "other": (
        "Question is a straightforward benefit or application lookup not "
        "covered by the other types."
    ),
}

TAXONOMY_BY_CASE_ID = {
    "R001": "eligibility_or_rule",
    "R002": "similar_policy",
    "R003": "similar_policy",
    "R004": "other",
    "R005": "eligibility_or_rule",
    "R006": "eligibility_or_rule",
    "R007": "similar_policy",
    "R008": "similar_policy",
    "R009": "other",
    "R010": "condition_missing",
    "R011": "similar_policy",
    "R012": "ambiguous_question",
    "R013": "similar_policy",
    "R014": "other",
    "R015": "condition_missing",
    "R016": "similar_policy",
    "R017": "similar_policy",
    "R018": "eligibility_or_rule",
    "R019": "other",
    "R020": "similar_policy",
    "R021": "similar_policy",
    "R022": "condition_missing",
    "R023": "eligibility_or_rule",
    "R024": "condition_missing",
    "R025": "other",
    "R026": "similar_policy",
    "R027": "other",
    "R028": "similar_policy",
    "R029": "similar_policy",
    "R030": "condition_missing",
    "R031": "eligibility_or_rule",
    "R032": "eligibility_or_rule",
    "R033": "eligibility_or_rule",
    "R034": "condition_missing",
    "R035": "other",
    "R036": "similar_policy",
    "R037": "ambiguous_question",
    "R038": "similar_policy",
    "R039": "other",
    "R040": "condition_missing",
    "R041": "eligibility_or_rule",
    "R042": "other",
    "R043": "other",
    "R044": "condition_missing",
    "R045": "other",
    "R046": "eligibility_or_rule",
    "R047": "other",
    "R048": "condition_missing",
    "R049": "other",
    "R050": "condition_missing",
}


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any] | list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_taxonomy(cases: list[dict[str, Any]]) -> dict[str, Any]:
    taxonomy_cases = []
    for case in cases:
        case_type = TAXONOMY_BY_CASE_ID[case["case_id"]]
        taxonomy_cases.append(
            {
                "case_id": case["case_id"],
                "type": case_type,
                "category": case["category"],
                "query": case["query"],
                "expected_policy_ids": case["expected_policy_ids"],
                "expected_sections": case["expected_sections"],
            }
        )

    counts = Counter(case["type"] for case in taxonomy_cases)
    return {
        "dataset": str(DEFAULT_CASES_PATH),
        "case_count": len(taxonomy_cases),
        "criteria": CASE_TYPE_CRITERIA,
        "counts": {case_type: counts.get(case_type, 0) for case_type in CASE_TYPE_CRITERIA},
        "cases": taxonomy_cases,
    }


def summarize_taxonomy(
    taxonomy: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    case_type_by_id = {
        case["case_id"]: case["type"]
        for case in taxonomy["cases"]
    }
    strategies: dict[str, Any] = {}
    for strategy in benchmark["strategies"]:
        by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for case in strategy["cases"]:
            by_type[case_type_by_id[case["case_id"]]].append(case)
        strategies[strategy["strategy"]] = {
            case_type: _hit_summary(cases)
            for case_type, cases in sorted(by_type.items())
        }
    return {
        "dataset": benchmark["dataset"],
        "source_benchmark": str(DEFAULT_BROAD_PATH),
        "case_count": taxonomy["case_count"],
        "counts": taxonomy["counts"],
        "strategies": strategies,
    }


def build_fallback_cases(
    broad: dict[str, Any],
    scoped: dict[str, Any],
) -> dict[str, Any]:
    return {
        "note": (
            "Fallback means AdaptivePolicyRetriever issued an additional SQL "
            "keyword lookup after vector retrieval. This is retrieval evidence, "
            "not generated-answer faithfulness."
        ),
        "benchmarks": [
            _fallback_summary("broad", broad, str(DEFAULT_BROAD_PATH)),
            _fallback_summary("scoped", scoped, str(DEFAULT_SCOPED_PATH)),
        ],
    }


def build_evidence_quality(
    broad: dict[str, Any],
    scoped: dict[str, Any],
) -> dict[str, Any]:
    return {
        "evaluation_name": "retrieved_evidence_correctness",
        "not_evaluated": "generated_answer_faithfulness",
        "criteria": {
            "policy_evidence_hit": "top-k contains an expected policy",
            "section_evidence_hit": (
                "top-k contains an expected policy with an expected section"
            ),
            "answer_ready_evidence": (
                "section_evidence_hit is true and no retrieval error occurred"
            ),
            "answer_quality_risk": (
                "missing expected policy, missing expected section, or error"
            ),
        },
        "benchmarks": [
            _evidence_summary("broad", broad, str(DEFAULT_BROAD_PATH)),
            _evidence_summary("scoped", scoped, str(DEFAULT_SCOPED_PATH)),
        ],
    }


def build_strategy_decision_summary(
    broad: dict[str, Any],
    scoped: dict[str, Any],
    repeated_broad: dict[str, Any] | None = None,
    repeated_scoped: dict[str, Any] | None = None,
) -> dict[str, Any]:
    broad_metrics = _metrics_by_strategy(broad)
    scoped_metrics = _metrics_by_strategy(scoped)
    vector = broad_metrics["vector"]
    hybrid = broad_metrics["hybrid"]
    adaptive = broad_metrics["adaptive"]
    scoped_vector = scoped_metrics["vector"]
    scoped_adaptive = scoped_metrics["adaptive"]
    summary = {
        "scope": "current 50-case internal retrieval evaluation",
        "decision_candidate": "adaptive",
        "baseline": "vector",
        "accuracy_latency_tradeoff": {
            "hybrid_policy_hit_gain_pct_point_vs_vector": _delta(
                hybrid, vector, "policy_hit_at_k_pct"
            ),
            "hybrid_section_hit_gain_pct_point_vs_vector": _delta(
                hybrid, vector, "section_hit_at_k_pct"
            ),
            "hybrid_mrr_gain_vs_vector": round(
                hybrid["mrr"] - vector["mrr"],
                4,
            ),
            "hybrid_p50_latency_increase_ms_vs_vector": _delta(
                hybrid, vector, "latency_p50_ms"
            ),
            "hybrid_p95_latency_increase_ms_vs_vector": _delta(
                hybrid, vector, "latency_p95_ms"
            ),
        },
        "adaptive_quality_preservation": {
            "broad_policy_hit_delta_pct_point_vs_vector": _delta(
                adaptive, vector, "policy_hit_at_k_pct"
            ),
            "broad_section_hit_delta_pct_point_vs_vector": _delta(
                adaptive, vector, "section_hit_at_k_pct"
            ),
            "broad_fallback_count": adaptive.get("fallback_count"),
            "broad_fallback_rate_pct": adaptive.get("fallback_rate_pct"),
            "scoped_section_hit_delta_pct_point_vs_vector": _delta(
                scoped_adaptive, scoped_vector, "section_hit_at_k_pct"
            ),
            "scoped_fallback_count": scoped_adaptive.get("fallback_count"),
            "scoped_fallback_rate_pct": scoped_adaptive.get("fallback_rate_pct"),
        },
        "bounded_decision_sentence": (
            "현재 50문항 내부 평가 조건에서는 Hybrid가 Policy Hit@5와 MRR은 "
            "소폭 높지만 p50 latency 증가가 커서 기본값으로 채택하지 않고, "
            "Vector 품질을 유지하면서 조건부 SQL 보충만 수행한 Adaptive를 "
            "챗봇 기본 검색 전략 후보로 둔다."
        ),
    }
    if repeated_broad and repeated_scoped:
        repeated_broad_metrics = _repeated_metrics_by_strategy(repeated_broad)
        repeated_scoped_metrics = _repeated_metrics_by_strategy(repeated_scoped)
        repeated_vector = repeated_broad_metrics["vector"]
        repeated_hybrid = repeated_broad_metrics["hybrid"]
        repeated_adaptive = repeated_broad_metrics["adaptive"]
        repeated_scoped_vector = repeated_scoped_metrics["vector"]
        repeated_scoped_adaptive = repeated_scoped_metrics["adaptive"]
        summary["repeated_latency_tradeoff"] = {
            "actual_repeated_run_available": True,
            "broad_source": str(DEFAULT_REPEATED_BROAD_PATH),
            "scoped_source": str(DEFAULT_REPEATED_SCOPED_PATH),
            "run_count": repeated_broad["run_count"],
            "warmup_run_count": repeated_broad["warmup_run_count"],
            "case_timeout_seconds": repeated_broad.get("case_timeout_seconds"),
            "hybrid_p50_latency_increase_ms_vs_vector": _delta(
                repeated_hybrid,
                repeated_vector,
                "latency_p50_ms",
            ),
            "hybrid_p95_latency_increase_ms_vs_vector": _delta(
                repeated_hybrid,
                repeated_vector,
                "latency_p95_ms",
            ),
            "adaptive_p50_latency_increase_ms_vs_vector": _delta(
                repeated_adaptive,
                repeated_vector,
                "latency_p50_ms",
            ),
            "adaptive_p95_latency_increase_ms_vs_vector": _delta(
                repeated_adaptive,
                repeated_vector,
                "latency_p95_ms",
            ),
            "scoped_adaptive_p50_latency_increase_ms_vs_vector": _delta(
                repeated_scoped_adaptive,
                repeated_scoped_vector,
                "latency_p50_ms",
            ),
            "scoped_adaptive_p95_latency_increase_ms_vs_vector": _delta(
                repeated_scoped_adaptive,
                repeated_scoped_vector,
                "latency_p95_ms",
            ),
        }
        summary["benchmark_execution_improvements"] = {
            "root_cause": (
                "Repeated benchmark re-embedded the same 50 queries for each "
                "vector-based strategy and run, then executed vector DB search "
                "sequentially."
            ),
            "observed_embedding_call_scale": {
                "broad_estimated_embedding_calls": 900,
                "scoped_estimated_embedding_calls": 600,
                "total_estimated_embedding_calls": 1500,
            },
            "implemented_controls": [
                "case_timeout_seconds records slow case failures instead of hanging",
                "checkpoint_output preserves completed strategy-run results",
            ],
            "recommended_next_improvements": [
                "cache query embeddings per case and reuse them across strategies",
                "measure embedding latency and vector DB latency separately",
                "rebuild final summaries from checkpoint JSONL after interruption",
                "replace SQL ILIKE keyword scan with PostgreSQL FTS or trigram index",
            ],
        }
    return summary


def build_latency_repeated_fixture(
    broad: dict[str, Any],
    scoped: dict[str, Any],
) -> dict[str, Any]:
    return {
        "execution_mode": "fixture_from_existing_single_run_benchmarks",
        "repeat_run_count": 1,
        "actual_repeated_run_available": False,
        "why_not_repeated": (
            "This artifact was generated from checked-in benchmark JSON without "
            "opening the DB, calling the embedding API, or overwriting the "
            "original benchmark files. Run retrieval_eval.py --repeat-runs 5 "
            "against a configured DB/API environment for true repeated latency."
        ),
        "recommended_command": (
            "PYTHONPATH=. .venv/bin/python tests/eval/retrieval_eval.py "
            "--repeat-runs 5 --top-k 5 --output "
            "output/retrieval_latency_repeated.json"
        ),
        "summaries": [
            _latency_fixture_summary("broad", broad, str(DEFAULT_BROAD_PATH)),
            _latency_fixture_summary("scoped", scoped, str(DEFAULT_SCOPED_PATH)),
        ],
    }


def build_latency_repeated_actual(
    repeated_broad: dict[str, Any],
    repeated_scoped: dict[str, Any],
) -> dict[str, Any]:
    _validate_repeated_benchmark(repeated_broad, expected_scope=False)
    _validate_repeated_benchmark(repeated_scoped, expected_scope=True)
    return {
        "execution_mode": "actual_repeated_benchmark",
        "repeat_run_count": repeated_broad["run_count"],
        "warmup_run_count": repeated_broad["warmup_run_count"],
        "actual_repeated_run_available": True,
        "case_timeout_seconds": repeated_broad.get("case_timeout_seconds"),
        "aggregation_note": repeated_broad["aggregation_note"],
        "summaries": [
            _latency_actual_summary(
                "broad",
                repeated_broad,
                str(DEFAULT_REPEATED_BROAD_PATH),
            ),
            _latency_actual_summary(
                "scoped",
                repeated_scoped,
                str(DEFAULT_REPEATED_SCOPED_PATH),
            ),
        ],
    }


def build_all(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    broad_path: Path = DEFAULT_BROAD_PATH,
    scoped_path: Path = DEFAULT_SCOPED_PATH,
    repeated_broad_path: Path = DEFAULT_REPEATED_BROAD_PATH,
    repeated_scoped_path: Path = DEFAULT_REPEATED_SCOPED_PATH,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    broad = load_json(broad_path)
    scoped = load_json(scoped_path)
    repeated_broad = load_json(repeated_broad_path) if repeated_broad_path.exists() else None
    repeated_scoped = (
        load_json(repeated_scoped_path) if repeated_scoped_path.exists() else None
    )
    taxonomy = build_taxonomy(cases)
    return {
        "taxonomy": taxonomy,
        "taxonomy_summary": summarize_taxonomy(taxonomy, broad),
        "fallback_cases": build_fallback_cases(broad, scoped),
        "evidence_quality": build_evidence_quality(broad, scoped),
        "strategy_decision_summary": build_strategy_decision_summary(
            broad,
            scoped,
            repeated_broad,
            repeated_scoped,
        ),
        "latency_repeated": (
            build_latency_repeated_actual(repeated_broad, repeated_scoped)
            if repeated_broad and repeated_scoped
            else build_latency_repeated_fixture(broad, scoped)
        ),
    }


def _hit_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(cases),
        "policy_hit_at_k_pct": _pct(
            sum(bool(case["policy_hit"]) for case in cases),
            len(cases),
        ),
        "section_hit_at_k_pct": _pct(
            sum(bool(case["section_hit"]) for case in cases),
            len(cases),
        ),
        "risk_case_ids": [
            case["case_id"]
            for case in cases
            if case["error"] is not None or not case["section_hit"]
        ],
    }


def _fallback_summary(label: str, benchmark: dict[str, Any], source: str) -> dict[str, Any]:
    adaptive = next(
        strategy
        for strategy in benchmark["strategies"]
        if strategy["strategy"] == "adaptive"
    )
    fallback_cases = [
        case
        for case in adaptive["cases"]
        if case.get("fallback_used")
    ]
    metrics = adaptive["metrics"]
    return {
        "label": label,
        "source_benchmark": source,
        "scope_to_expected_policy": benchmark["scope_to_expected_policy"],
        "case_count": benchmark["case_count"],
        "fallback_count": metrics.get("fallback_count", len(fallback_cases)),
        "fallback_rate_pct": metrics.get("fallback_rate_pct"),
        "cases": [
            {
                "case_id": case["case_id"],
                "category": case["category"],
                "query": case["query"],
                "expected_policy_ids": case["expected_policy_ids"],
                "expected_sections": case["expected_sections"],
                "policy_hit": case["policy_hit"],
                "section_hit": case["section_hit"],
                "risk": _risk_label(case),
                "retrieved_summary": [
                    {
                        "rank": hit["rank"],
                        "policy_id": hit["policy_id"],
                        "section": hit["section"],
                    }
                    for hit in case["retrieved"]
                ],
            }
            for case in fallback_cases
        ],
    }


def _evidence_summary(label: str, benchmark: dict[str, Any], source: str) -> dict[str, Any]:
    strategies = []
    for strategy in benchmark["strategies"]:
        cases = strategy["cases"]
        risk_cases = [
            case
            for case in cases
            if case["error"] is not None or not case["section_hit"]
        ]
        strategies.append(
            {
                "strategy": strategy["strategy"],
                "case_count": len(cases),
                "policy_evidence_hit_pct": _pct(
                    sum(bool(case["policy_hit"]) for case in cases),
                    len(cases),
                ),
                "section_evidence_hit_pct": _pct(
                    sum(bool(case["section_hit"]) for case in cases),
                    len(cases),
                ),
                "answer_ready_evidence_pct": _pct(
                    sum(case["error"] is None and case["section_hit"] for case in cases),
                    len(cases),
                ),
                "answer_quality_risk_count": len(risk_cases),
                "answer_quality_risk_rate_pct": _pct(len(risk_cases), len(cases)),
                "risk_cases": [
                    {
                        "case_id": case["case_id"],
                        "query": case["query"],
                        "expected_policy_ids": case["expected_policy_ids"],
                        "expected_sections": case["expected_sections"],
                        "risk": _risk_label(case),
                        "top_result": case["retrieved"][0] if case["retrieved"] else None,
                    }
                    for case in risk_cases
                ],
            }
        )
    return {
        "label": label,
        "source_benchmark": source,
        "scope_to_expected_policy": benchmark["scope_to_expected_policy"],
        "strategies": strategies,
    }


def _latency_fixture_summary(label: str, benchmark: dict[str, Any], source: str) -> dict[str, Any]:
    return {
        "label": label,
        "source_benchmark": source,
        "scope_to_expected_policy": benchmark["scope_to_expected_policy"],
        "case_count": benchmark["case_count"],
        "strategies": [
            {
                "strategy": strategy["strategy"],
                "single_run_latency_p50_ms": strategy["metrics"]["latency_p50_ms"],
                "single_run_latency_p95_ms": strategy["metrics"]["latency_p95_ms"],
                "fallback_count": strategy["metrics"].get("fallback_count"),
                "fallback_rate_pct": strategy["metrics"].get("fallback_rate_pct"),
            }
            for strategy in benchmark["strategies"]
        ],
    }


def _latency_actual_summary(
    label: str,
    benchmark: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    return {
        "label": label,
        "source_benchmark": source,
        "scope_to_expected_policy": benchmark["scope_to_expected_policy"],
        "case_count": benchmark["case_count"],
        "run_count": benchmark["run_count"],
        "warmup_run_count": benchmark["warmup_run_count"],
        "strategies": [
            {
                "strategy": strategy["strategy"],
                "policy_hit_at_k_pct_values": strategy["policy_hit_at_k_pct_values"],
                "section_hit_at_k_pct_values": strategy["section_hit_at_k_pct_values"],
                "mrr_values": strategy["mrr_values"],
                "error_rate_pct_values": strategy["error_rate_pct_values"],
                "latency_p50_ms_values": strategy["latency_p50_ms_values"],
                "latency_p95_ms_values": strategy["latency_p95_ms_values"],
                "median_latency_p50_ms": strategy["latency_p50_ms"],
                "median_latency_p95_ms": strategy["latency_p95_ms"],
                "fallback_count_values": strategy["fallback_count_values"],
                "fallback_rate_pct_values": strategy["fallback_rate_pct_values"],
            }
            for strategy in benchmark["strategies"]
        ],
    }


def _metrics_by_strategy(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        strategy["strategy"]: strategy["metrics"]
        for strategy in benchmark["strategies"]
    }


def _repeated_metrics_by_strategy(benchmark: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        strategy["strategy"]: strategy
        for strategy in benchmark["strategies"]
    }


def _validate_repeated_benchmark(
    benchmark: dict[str, Any],
    *,
    expected_scope: bool,
) -> None:
    if benchmark.get("execution_mode") != "actual_repeated_benchmark":
        raise ValueError("repeated benchmark must be an actual repeated run")
    if benchmark.get("run_count", 0) < 3:
        raise ValueError("repeated benchmark needs at least 3 measured runs")
    if benchmark.get("case_count") != 50:
        raise ValueError("repeated benchmark must use the 50-case goldset")
    if benchmark.get("top_k") != 5:
        raise ValueError("repeated benchmark must use top_k=5")
    if benchmark.get("scope_to_expected_policy") is not expected_scope:
        raise ValueError("repeated benchmark scope does not match expected input")
    for strategy in benchmark.get("strategies", []):
        required = {
            "policy_hit_at_k_pct_values",
            "section_hit_at_k_pct_values",
            "mrr_values",
            "error_rate_pct_values",
            "latency_p50_ms_values",
            "latency_p95_ms_values",
            "latency_p50_ms",
            "latency_p95_ms",
        }
        if missing := sorted(required - set(strategy)):
            raise ValueError(f"repeated strategy missing fields: {missing}")


def _delta(left: dict[str, Any], right: dict[str, Any], key: str) -> float:
    return round(float(left[key]) - float(right[key]), 3)


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _risk_label(case: dict[str, Any]) -> str:
    if case["error"] is not None:
        return "retrieval_error"
    if not case["policy_hit"]:
        return "missing_expected_policy"
    if not case["section_hit"]:
        return "missing_expected_section"
    return "answer_ready_evidence"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--broad", type=Path, default=DEFAULT_BROAD_PATH)
    parser.add_argument("--scoped", type=Path, default=DEFAULT_SCOPED_PATH)
    parser.add_argument("--repeated-broad", type=Path, default=DEFAULT_REPEATED_BROAD_PATH)
    parser.add_argument("--repeated-scoped", type=Path, default=DEFAULT_REPEATED_SCOPED_PATH)
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument(
        "--taxonomy-output",
        type=Path,
        default=Path("tests/eval/retrieval_case_taxonomy.json"),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    artifacts = build_all(
        cases_path=args.cases,
        broad_path=args.broad,
        scoped_path=args.scoped,
        repeated_broad_path=args.repeated_broad,
        repeated_scoped_path=args.repeated_scoped,
    )
    write_json(args.taxonomy_output, artifacts["taxonomy"])
    write_json(
        args.output_dir / "retrieval_case_taxonomy_summary.json",
        artifacts["taxonomy_summary"],
    )
    write_json(
        args.output_dir / "retrieval_fallback_cases.json",
        artifacts["fallback_cases"],
    )
    write_json(
        args.output_dir / "retrieval_evidence_quality.json",
        artifacts["evidence_quality"],
    )
    write_json(
        args.output_dir / "retrieval_strategy_decision_summary.json",
        artifacts["strategy_decision_summary"],
    )
    write_json(
        args.output_dir / "retrieval_latency_repeated.json",
        artifacts["latency_repeated"],
    )


if __name__ == "__main__":
    main()

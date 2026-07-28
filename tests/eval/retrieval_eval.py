"""정책 챗봇 리트리버 3방식 정량 비교.

동일한 골드셋을 SQL 키워드, pgvector, RRF 하이브리드 리트리버에 적용해
정책 Hit@K, 근거 섹션 Hit@K, MRR, 지연시간을 측정한다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from app.ai.retrievers import RetrievalHit, RetrievalStrategy, build_policy_retriever
from app.common.psycopg_pool_conf import psycopg_pool


DEFAULT_CASES_PATH = Path(__file__).with_name("retrieval_cases.jsonl")


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    case_id: str
    query: str
    expected_policy_id: int
    expected_sections: tuple[str, ...]
    category: str


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[RetrievalCase]:
    cases: list[RetrievalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        try:
            cases.append(
                RetrievalCase(
                    case_id=str(payload["case_id"]),
                    query=str(payload["query"]),
                    expected_policy_id=int(payload["expected_policy_id"]),
                    expected_sections=tuple(payload["expected_sections"]),
                    category=str(payload["category"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid retrieval case at line {line_number}") from exc

    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("retrieval case_id values must be unique")
    return cases


async def evaluate_strategy(
    strategy: RetrievalStrategy,
    cases: Sequence[RetrievalCase],
    *,
    top_k: int,
) -> dict[str, Any]:
    retriever = build_policy_retriever(strategy)
    case_results: list[dict[str, Any]] = []

    for case in cases:
        started_at = time.perf_counter()
        try:
            hits = await retriever.retrieve(case.query, top_k=top_k)
            error = None
        except Exception as exc:
            hits = []
            error = f"{type(exc).__name__}: {exc}"
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        case_results.append(
            score_case(
                case,
                hits,
                top_k=top_k,
                elapsed_ms=elapsed_ms,
                error=error,
            )
        )

    return {
        "strategy": strategy.value,
        "metrics": aggregate_metrics(case_results),
        "cases": case_results,
    }


def score_case(
    case: RetrievalCase,
    hits: Sequence[RetrievalHit],
    *,
    top_k: int,
    elapsed_ms: float,
    error: str | None = None,
) -> dict[str, Any]:
    ranked_hits = list(hits[:top_k])
    policy_rank = _first_rank(
        ranked_hits,
        lambda hit: hit.policy_id == case.expected_policy_id,
    )
    section_rank = _first_rank(
        ranked_hits,
        lambda hit: (
            hit.policy_id == case.expected_policy_id
            and hit.section in case.expected_sections
        ),
    )
    return {
        "case_id": case.case_id,
        "category": case.category,
        "query": case.query,
        "expected_policy_id": case.expected_policy_id,
        "expected_sections": list(case.expected_sections),
        "policy_hit": policy_rank is not None,
        "section_hit": section_rank is not None,
        "policy_rank": policy_rank,
        "section_rank": section_rank,
        "reciprocal_rank": 0.0 if policy_rank is None else 1.0 / policy_rank,
        "elapsed_ms": round(elapsed_ms, 3),
        "error": error,
        "retrieved": [
            {
                "rank": rank,
                "policy_id": hit.policy_id,
                "chunk_id": hit.chunk_id,
                "section": hit.section,
                "score": round(hit.score, 6),
            }
            for rank, hit in enumerate(ranked_hits, start=1)
        ],
    }


def aggregate_metrics(case_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = len(case_results)
    if count == 0:
        return {
            "case_count": 0,
            "policy_hit_at_k_pct": 0.0,
            "section_hit_at_k_pct": 0.0,
            "mrr": 0.0,
            "error_rate_pct": 0.0,
            "latency_mean_ms": 0.0,
            "latency_p50_ms": 0.0,
            "latency_p95_ms": 0.0,
        }

    latencies = [float(result["elapsed_ms"]) for result in case_results]
    return {
        "case_count": count,
        "policy_hit_at_k_pct": _pct(
            sum(bool(result["policy_hit"]) for result in case_results),
            count,
        ),
        "section_hit_at_k_pct": _pct(
            sum(bool(result["section_hit"]) for result in case_results),
            count,
        ),
        "mrr": round(
            statistics.fmean(
                float(result["reciprocal_rank"]) for result in case_results
            ),
            4,
        ),
        "error_rate_pct": _pct(
            sum(result["error"] is not None for result in case_results),
            count,
        ),
        "latency_mean_ms": round(statistics.fmean(latencies), 3),
        "latency_p50_ms": round(statistics.median(latencies), 3),
        "latency_p95_ms": round(_percentile(latencies, 0.95), 3),
        "by_category": _category_metrics(case_results),
    }


def _category_metrics(
    case_results: Sequence[dict[str, Any]],
) -> dict[str, dict[str, float | int]]:
    categories = sorted({str(result["category"]) for result in case_results})
    metrics: dict[str, dict[str, float | int]] = {}
    for category in categories:
        selected = [
            result for result in case_results if result["category"] == category
        ]
        metrics[category] = {
            "case_count": len(selected),
            "policy_hit_at_k_pct": _pct(
                sum(bool(result["policy_hit"]) for result in selected),
                len(selected),
            ),
            "section_hit_at_k_pct": _pct(
                sum(bool(result["section_hit"]) for result in selected),
                len(selected),
            ),
        }
    return metrics


def _first_rank(
    hits: Sequence[RetrievalHit],
    predicate,
) -> int | None:
    return next(
        (
            rank
            for rank, hit in enumerate(hits, start=1)
            if predicate(hit)
        ),
        None,
    )


def _pct(numerator: int, denominator: int) -> float:
    return round(numerator / denominator * 100, 2) if denominator else 0.0


def _percentile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return ordered[index]


async def run_benchmark(
    cases: Sequence[RetrievalCase],
    *,
    top_k: int,
    strategies: Sequence[RetrievalStrategy],
) -> dict[str, Any]:
    await psycopg_pool.open(wait=True, timeout=10)
    try:
        results = [
            await evaluate_strategy(strategy, cases, top_k=top_k)
            for strategy in strategies
        ]
    finally:
        await psycopg_pool.close()
    return {
        "dataset": "tests/eval/retrieval_cases.jsonl",
        "top_k": top_k,
        "case_count": len(cases),
        "strategies": results,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--strategies",
        nargs="+",
        choices=[strategy.value for strategy in RetrievalStrategy],
        default=[strategy.value for strategy in RetrievalStrategy],
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cases = load_cases(args.cases)
    if args.ids:
        selected_ids = set(args.ids)
        cases = [case for case in cases if case.case_id in selected_ids]
    if not cases:
        raise SystemExit("no retrieval cases selected")
    if args.top_k <= 0:
        raise SystemExit("--top-k must be positive")

    result = asyncio.run(
        run_benchmark(
            cases,
            top_k=args.top_k,
            strategies=[
                RetrievalStrategy(strategy) for strategy in args.strategies
            ],
        )
    )
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

import asyncio

from app.ai.retrievers import RetrievalHit, RetrievalStrategy
from tests.eval.retrieval_eval import (
    DEFAULT_CASES_PATH,
    RetrievalCase,
    _run_benchmark_once,
    aggregate_metrics,
    evaluate_strategy,
    load_cases,
    run_repeated_benchmark,
    score_case,
    summarize_repeated_runs,
    validate_run_counts,
)


def _hit(
    policy_id: int,
    chunk_id: int,
    section: str,
) -> RetrievalHit:
    return RetrievalHit(
        chunk_id=chunk_id,
        policy_id=policy_id,
        chunk_text="근거",
        score=0.9,
        section=section,
    )


def test_retrieval_goldset_has_exactly_50_unique_cases():
    cases = load_cases(DEFAULT_CASES_PATH)

    assert len(cases) == 50
    assert len({case.case_id for case in cases}) == 50
    assert len(
        {
            policy_id
            for case in cases
            for policy_id in case.expected_policy_ids
        }
    ) == 12


def test_retrieval_queries_do_not_reveal_exact_policy_names():
    policy_names = {
        237: "발달장애인 자기주도 재직자 훈련",
        238: "법문화교육(교육센터)",
        240: "건강보험 임신출산 진료비(국민행복카드)",
        242: "무료법률상담",
        246: "난임치료휴가급여 지원",
        247: "위기임신 및 보호출산 지원",
        251: "인플루엔자 국가예방접종 지원사업",
        255: "첫만남이용권",
        258: "의료급여임신.출산진료비지원",
        287: "부모급여 지원",
        289: "에너지바우처",
        292: "스포츠강좌이용권",
    }

    for case in load_cases(DEFAULT_CASES_PATH):
        for policy_id in case.expected_policy_ids:
            assert policy_names[policy_id] not in case.query


def test_score_case_tracks_policy_and_section_hit_separately():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_ids=(100,),
        expected_sections=("지원 내용",),
        category="benefit",
    )

    result = score_case(
        case,
        [
            _hit(200, 1, "지원 내용"),
            _hit(100, 2, "신청 방법"),
            _hit(100, 3, "지원 내용"),
        ],
        top_k=3,
        elapsed_ms=12.34,
    )

    assert result["policy_hit"] is True
    assert result["section_hit"] is True
    assert result["policy_rank"] == 2
    assert result["section_rank"] == 3
    assert result["reciprocal_rank"] == 0.5


def test_score_case_accepts_any_reviewed_policy_answer():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_ids=(100, 200),
        expected_sections=("지원 내용",),
        category="benefit",
    )

    result = score_case(
        case,
        [_hit(200, 1, "지원 내용")],
        top_k=1,
        elapsed_ms=10,
    )

    assert result["policy_hit"] is True
    assert result["section_hit"] is True
    assert result["policy_rank"] == 1


def test_aggregate_metrics_includes_failures_in_denominator():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_ids=(100,),
        expected_sections=("지원 내용",),
        category="benefit",
    )
    success = score_case(
        case,
        [_hit(100, 1, "지원 내용")],
        top_k=1,
        elapsed_ms=10,
    )
    failure = score_case(
        case,
        [],
        top_k=1,
        elapsed_ms=30,
        error="timeout",
    )

    metrics = aggregate_metrics([success, failure])

    assert metrics["policy_hit_at_k_pct"] == 50.0
    assert metrics["section_hit_at_k_pct"] == 50.0
    assert metrics["mrr"] == 0.5
    assert metrics["error_rate_pct"] == 50.0
    assert metrics["latency_p50_ms"] == 20.0
    assert metrics["latency_p95_ms"] == 30.0


def test_aggregate_metrics_reports_adaptive_fallback_rate():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_ids=(100,),
        expected_sections=("지원 내용",),
        category="benefit",
    )
    fallback = score_case(
        case,
        [_hit(100, 1, "지원 내용")],
        top_k=1,
        elapsed_ms=20,
    )
    fallback["fallback_used"] = True
    vector_only = score_case(
        case,
        [_hit(100, 2, "지원 내용")],
        top_k=1,
        elapsed_ms=10,
    )
    vector_only["fallback_used"] = False

    metrics = aggregate_metrics([fallback, vector_only])

    assert metrics["fallback_count"] == 1
    assert metrics["fallback_rate_pct"] == 50.0


def test_evaluate_strategy_can_scope_search_to_expected_policy(monkeypatch):
    calls: list[dict[str, object]] = []

    class FakeRetriever:
        async def retrieve(self, query, **kwargs):
            calls.append({"query": query, **kwargs})
            return [_hit(100, 1, "지원 대상")]

    monkeypatch.setattr(
        "tests.eval.retrieval_eval.build_policy_retriever",
        lambda strategy: FakeRetriever(),
    )
    case = RetrievalCase(
        case_id="R999",
        query="지원 자격",
        expected_policy_ids=(100,),
        expected_sections=("지원 대상",),
        category="target",
    )

    asyncio.run(
        evaluate_strategy(
            RetrievalStrategy.ADAPTIVE,
            [case],
            top_k=5,
            scope_to_expected_policy=True,
        )
    )

    assert calls[0]["policy_ids"] == [100]
    assert calls[0]["evidence_role"] == "TARGET"


def test_evaluate_strategy_records_case_timeout(monkeypatch):
    class SlowRetriever:
        async def retrieve(self, query, **kwargs):
            await asyncio.sleep(0.05)
            return [_hit(100, 1, "지원 대상")]

    monkeypatch.setattr(
        "tests.eval.retrieval_eval.build_policy_retriever",
        lambda strategy: SlowRetriever(),
    )
    case = RetrievalCase(
        case_id="R999",
        query="지원 자격",
        expected_policy_ids=(100,),
        expected_sections=("지원 대상",),
        category="target",
    )

    result = asyncio.run(
        evaluate_strategy(
            RetrievalStrategy.VECTOR,
            [case],
            top_k=5,
            case_timeout_seconds=0.001,
        )
    )

    assert result["metrics"]["error_rate_pct"] == 100.0
    assert result["cases"][0]["error"] == "TimeoutError: case exceeded 0.001s"
    assert result["cases"][0]["policy_hit"] is False


def test_summarize_repeated_runs_preserves_per_run_latency_values():
    run_template = {
        "dataset": "tests/eval/retrieval_cases.jsonl",
        "top_k": 5,
        "case_count": 1,
        "scope_to_expected_policy": False,
        "strategies": [
            {
                "strategy": "vector",
                "metrics": {
                    "case_count": 1,
                    "policy_hit_at_k_pct": 100.0,
                    "section_hit_at_k_pct": 100.0,
                    "mrr": 1.0,
                    "error_rate_pct": 0.0,
                    "latency_p50_ms": 100.0,
                    "latency_p95_ms": 200.0,
                },
            }
        ],
    }
    second_run = {
        **run_template,
        "strategies": [
            {
                "strategy": "vector",
                "metrics": {
                    **run_template["strategies"][0]["metrics"],
                    "latency_p50_ms": 120.0,
                    "latency_p95_ms": 260.0,
                },
            }
        ],
    }

    summary = summarize_repeated_runs([run_template, second_run])

    assert summary["run_count"] == 2
    assert summary["strategies"][0]["latency_p50_ms_values"] == [100.0, 120.0]
    assert summary["strategies"][0]["latency_p95_ms"] == 230.0


def test_summarize_repeated_runs_records_warmup_without_aggregating_it():
    run_template = {
        "dataset": "tests/eval/retrieval_cases.jsonl",
        "top_k": 5,
        "case_count": 1,
        "scope_to_expected_policy": False,
        "strategies": [
            {
                "strategy": "vector",
                "metrics": {
                    "case_count": 1,
                    "policy_hit_at_k_pct": 100.0,
                    "section_hit_at_k_pct": 100.0,
                    "mrr": 1.0,
                    "error_rate_pct": 0.0,
                    "latency_p50_ms": 100.0,
                    "latency_p95_ms": 200.0,
                },
            }
        ],
    }
    measured_slow = {
        **run_template,
        "strategies": [
            {
                "strategy": "vector",
                "metrics": {
                    **run_template["strategies"][0]["metrics"],
                    "latency_p50_ms": 140.0,
                    "latency_p95_ms": 260.0,
                },
            }
        ],
    }
    warmup = {
        **run_template,
        "strategies": [
            {
                "strategy": "vector",
                "metrics": {
                    **run_template["strategies"][0]["metrics"],
                    "latency_p50_ms": 9999.0,
                    "latency_p95_ms": 9999.0,
                },
            }
        ],
    }

    summary = summarize_repeated_runs(
        [run_template, measured_slow, run_template],
        warmup_runs=[warmup],
    )

    assert summary["execution_mode"] == "actual_repeated_benchmark"
    assert summary["run_count"] == 3
    assert summary["warmup_run_count"] == 1
    assert summary["total_run_count"] == 4
    assert summary["strategies"][0]["latency_p50_ms"] == 100.0
    assert summary["strategies"][0]["latency_p95_ms"] == 200.0
    assert summary["strategies"][0]["error_rate_pct_values"] == [0.0, 0.0, 0.0]
    assert (
        summary["warmup_runs"][0]["strategies"][0]["metrics"]["latency_p50_ms"]
        == 9999.0
    )


def test_run_repeated_benchmark_excludes_warmup_runs_from_summary(monkeypatch):
    calls = []

    class FakePool:
        async def open(self, **kwargs):
            calls.append(("open", kwargs))

        async def close(self):
            calls.append(("close", {}))

    async def fake_run_once(*args, **kwargs):
        call_number = sum(call[0] == "run" for call in calls)
        calls.append(("run", kwargs))
        latency = 9999.0 if call_number == 0 else 100.0 + call_number
        return {
            "dataset": "tests/eval/retrieval_cases.jsonl",
            "top_k": kwargs["top_k"],
            "case_count": 1,
            "scope_to_expected_policy": kwargs["scope_to_expected_policy"],
            "strategies": [
                {
                    "strategy": "vector",
                    "metrics": {
                        "case_count": 1,
                        "policy_hit_at_k_pct": 100.0,
                        "section_hit_at_k_pct": 100.0,
                        "mrr": 1.0,
                        "error_rate_pct": 0.0,
                        "latency_p50_ms": latency,
                        "latency_p95_ms": latency,
                    },
                }
            ],
        }

    monkeypatch.setattr("tests.eval.retrieval_eval.psycopg_pool", FakePool())
    monkeypatch.setattr("tests.eval.retrieval_eval._run_benchmark_once", fake_run_once)

    result = asyncio.run(
        run_repeated_benchmark(
            [
                RetrievalCase(
                    case_id="R999",
                    query="질문",
                    expected_policy_ids=(100,),
                    expected_sections=("지원 내용",),
                    category="benefit",
                )
            ],
            top_k=5,
            strategies=[RetrievalStrategy.VECTOR],
            repeat_runs=3,
            warmup_runs=1,
        )
    )

    assert [call[0] for call in calls].count("run") == 4
    assert result["run_count"] == 3
    assert result["warmup_run_count"] == 1
    assert result["strategies"][0]["latency_p50_ms_values"] == [
        101.0,
        102.0,
        103.0,
    ]


def test_run_repeated_benchmark_keeps_single_run_shape_without_warmup(monkeypatch):
    class FakePool:
        async def open(self, **kwargs):
            pass

        async def close(self):
            pass

    async def fake_run_once(*args, **kwargs):
        return {
            "dataset": "tests/eval/retrieval_cases.jsonl",
            "top_k": kwargs["top_k"],
            "case_count": 1,
            "scope_to_expected_policy": kwargs["scope_to_expected_policy"],
            "strategies": [
                {
                    "strategy": "vector",
                    "metrics": {
                        "case_count": 1,
                        "policy_hit_at_k_pct": 100.0,
                        "section_hit_at_k_pct": 100.0,
                        "mrr": 1.0,
                        "error_rate_pct": 0.0,
                        "latency_p50_ms": 100.0,
                        "latency_p95_ms": 100.0,
                    },
                }
            ],
        }

    monkeypatch.setattr("tests.eval.retrieval_eval.psycopg_pool", FakePool())
    monkeypatch.setattr("tests.eval.retrieval_eval._run_benchmark_once", fake_run_once)

    result = asyncio.run(
        run_repeated_benchmark(
            [
                RetrievalCase(
                    case_id="R999",
                    query="질문",
                    expected_policy_ids=(100,),
                    expected_sections=("지원 내용",),
                    category="benefit",
                )
            ],
            top_k=5,
            strategies=[RetrievalStrategy.VECTOR],
            repeat_runs=1,
        )
    )

    assert "execution_mode" not in result
    assert "run_count" not in result
    assert result["strategies"][0]["strategy"] == "vector"


def test_run_benchmark_once_writes_strategy_checkpoints(monkeypatch, tmp_path):
    async def fake_evaluate_strategy(strategy, cases, **kwargs):
        return {
            "strategy": strategy.value,
            "metrics": {
                "case_count": len(cases),
                "policy_hit_at_k_pct": 100.0,
                "section_hit_at_k_pct": 100.0,
                "mrr": 1.0,
                "error_rate_pct": 0.0,
                "latency_p50_ms": 10.0,
                "latency_p95_ms": 10.0,
            },
            "cases": [],
        }

    monkeypatch.setattr(
        "tests.eval.retrieval_eval.evaluate_strategy",
        fake_evaluate_strategy,
    )
    checkpoint = tmp_path / "checkpoint.jsonl"

    asyncio.run(
        _run_benchmark_once(
            [
                RetrievalCase(
                    case_id="R999",
                    query="질문",
                    expected_policy_ids=(100,),
                    expected_sections=("지원 내용",),
                    category="benefit",
                )
            ],
            top_k=5,
            strategies=[RetrievalStrategy.SQL_KEYWORD, RetrievalStrategy.VECTOR],
            scope_to_expected_policy=False,
            case_timeout_seconds=15,
            checkpoint_output=checkpoint,
            phase="measured",
            run_index=2,
        )
    )

    entries = [
        __import__("json").loads(line)
        for line in checkpoint.read_text(encoding="utf-8").splitlines()
    ]

    assert [entry["strategy"] for entry in entries] == ["sql_keyword", "vector"]
    assert entries[0]["phase"] == "measured"
    assert entries[0]["run_index"] == 2
    assert entries[0]["case_timeout_seconds"] == 15


def test_validate_run_counts_rejects_invalid_values():
    validate_run_counts(repeat_runs=1, warmup_runs=0)

    try:
        validate_run_counts(repeat_runs=0, warmup_runs=0)
    except ValueError as exc:
        assert str(exc) == "--repeat-runs must be positive"
    else:
        raise AssertionError("repeat_runs=0 should fail")

    try:
        validate_run_counts(repeat_runs=1, warmup_runs=-1)
    except ValueError as exc:
        assert str(exc) == "--warmup-runs must be zero or positive"
    else:
        raise AssertionError("warmup_runs=-1 should fail")

from app.ai.retrievers import RetrievalHit
from tests.eval.retrieval_eval import (
    DEFAULT_CASES_PATH,
    RetrievalCase,
    aggregate_metrics,
    load_cases,
    score_case,
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
    assert len({case.expected_policy_id for case in cases}) == 10


def test_score_case_tracks_policy_and_section_hit_separately():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_id=100,
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


def test_aggregate_metrics_includes_failures_in_denominator():
    case = RetrievalCase(
        case_id="R999",
        query="질문",
        expected_policy_id=100,
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

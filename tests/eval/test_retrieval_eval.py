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

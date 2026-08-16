"""Build generated-summary grounding manual review artifacts.

This module is intentionally offline. It defines the claim-level rubric and a
small reviewed sample set without calling the DB, retrieval API, or an LLM.
The output evaluates generated-answer grounding separately from retrieval
evidence correctness.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT_PATH = Path("output/generated_grounding_manual_rubric.json")
DEFAULT_REPORT_PATH = Path("docs/eval/generated_grounding_manual_rubric.md")

GROUNDING_STATUSES = {
    "grounded",
    "partially_grounded",
    "unsupported",
    "not_checkable",
}
SUPPORT_TYPES = {
    "policy_field",
    "retrieved_chunk",
    "both",
    "unsupported",
    "unclear",
}
ISSUE_TYPES = {
    "none",
    "hallucinated_condition",
    "wrong_amount",
    "wrong_period",
    "wrong_method",
    "overgeneralized",
    "generic_only",
}
CRITICAL_ISSUES = {
    "hallucinated_condition",
    "wrong_amount",
    "wrong_period",
    "wrong_method",
}


MANUAL_REVIEW_SAMPLES: list[dict[str, Any]] = [
    {
        "sample_id": "G001",
        "case_id": "R001",
        "policy_id": 237,
        "category": "target",
        "claim_text": "발달장애가 있는 근로자는 직장 적응을 위한 교육 지원 대상이에요.",
        "support_type": "both",
        "support_reference": {
            "policy_fields": ["condition_profile_target_summary"],
            "chunk_ids": [23790000002, 23790000000],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "대상 조건과 공식 지원대상 원문 chunk가 같은 방향으로 지지한다.",
    },
    {
        "sample_id": "G002",
        "case_id": "R002",
        "policy_id": 237,
        "category": "benefit",
        "claim_text": "직장 예절과 대인관계처럼 업무 적응에 필요한 내용을 배울 수 있어요.",
        "support_type": "retrieved_chunk",
        "support_reference": {
            "policy_fields": [],
            "chunk_ids": [23790000003],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "지원 내용 chunk가 교육 내용 claim을 직접 뒷받침한다.",
    },
    {
        "sample_id": "G003",
        "case_id": "R004",
        "policy_id": 237,
        "category": "application",
        "claim_text": "신청이나 문의는 정책의 신청 방법 안내를 기준으로 확인해야 해요.",
        "support_type": "retrieved_chunk",
        "support_reference": {
            "policy_fields": ["application_method"],
            "chunk_ids": [23790000004],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "신청 방법 chunk가 존재하지만 구체 기관명은 claim에 추가하지 않았다.",
    },
    {
        "sample_id": "G004",
        "case_id": "R006",
        "policy_id": 238,
        "category": "target",
        "claim_text": "한국 법이 낯선 이주민이나 북한이탈주민도 쉬운 법 교육 대상에 포함돼요.",
        "support_type": "both",
        "support_reference": {
            "policy_fields": ["condition_profile_target_summary"],
            "chunk_ids": [23890000000, 23890000003],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "정리된 지원 조건과 공식 지원대상 원문 모두 대상 claim을 지지한다.",
    },
    {
        "sample_id": "G005",
        "case_id": "R010",
        "policy_id": 238,
        "category": "application",
        "claim_text": "모집 기간이 정해져 있지 않다는 안내는 신청 기간 근거를 확인해야 해요.",
        "support_type": "retrieved_chunk",
        "support_reference": {
            "policy_fields": ["application_period_text"],
            "chunk_ids": [23890000006],
        },
        "grounding_status": "partially_grounded",
        "issue_type": "overgeneralized",
        "review_note": "신청 기간 영역과 연결되지만 상시 모집으로 단정하면 추가 확인이 필요하다.",
    },
    {
        "sample_id": "G006",
        "case_id": "R012",
        "policy_id": 240,
        "category": "benefit",
        "claim_text": "비슷한 지원이 여러 개일 수 있어 정책명과 지원 내용을 함께 확인해야 해요.",
        "support_type": "policy_field",
        "support_reference": {
            "policy_fields": ["name", "benefit_description"],
            "chunk_ids": [],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "ambiguous case라 단정 답변보다 확인 안내가 근거 경계에 맞다.",
    },
    {
        "sample_id": "G007",
        "case_id": "R021",
        "policy_id": 246,
        "category": "benefit",
        "claim_text": "정책 상세 안내에 있는 대상, 혜택, 신청 정보를 읽기 쉬운 문장으로 정리했어요.",
        "support_type": "unclear",
        "support_reference": {
            "policy_fields": [],
            "chunk_ids": [],
        },
        "grounding_status": "not_checkable",
        "issue_type": "generic_only",
        "review_note": "일반 fallback 문구라 정책별 claim으로 채점하지 않는다.",
    },
    {
        "sample_id": "G008",
        "case_id": "R031",
        "policy_id": 251,
        "category": "target",
        "claim_text": "지원 대상은 정리된 조건과 공식 지원대상 원문을 함께 확인해야 해요.",
        "support_type": "retrieved_chunk",
        "support_reference": {
            "policy_fields": ["condition_profile_target_summary"],
            "chunk_ids": [25190000000, 25190000003],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "대상 조건 claim이고 수치나 기관명을 새로 만들지 않았다.",
    },
    {
        "sample_id": "G009",
        "case_id": "R041",
        "policy_id": 289,
        "category": "benefit",
        "claim_text": "전기와 난방 비용 지원 여부는 지원 내용 근거에서 확인할 수 있어요.",
        "support_type": "retrieved_chunk",
        "support_reference": {
            "policy_fields": ["benefit_description"],
            "chunk_ids": [28990000004],
        },
        "grounding_status": "grounded",
        "issue_type": "none",
        "review_note": "지원 내용 chunk가 에너지 비용 지원 claim과 연결된다.",
    },
    {
        "sample_id": "G010",
        "case_id": "R047",
        "policy_id": 292,
        "category": "benefit",
        "claim_text": "체육 수업 비용은 매달 30만원씩 1년 동안 지원돼요.",
        "support_type": "unsupported",
        "support_reference": {
            "policy_fields": ["benefit_description"],
            "chunk_ids": [29290000004],
        },
        "grounding_status": "unsupported",
        "issue_type": "wrong_amount",
        "review_note": "금액을 단정한 claim은 원문 근거 대조 없이는 critical error로 본다.",
    },
]


def build_manual_grounding_report(
    samples: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    reviewed_samples = list(samples or MANUAL_REVIEW_SAMPLES)
    _validate_samples(reviewed_samples)
    checkable = [
        sample
        for sample in reviewed_samples
        if sample["grounding_status"] != "not_checkable"
    ]
    grounded = [
        sample
        for sample in checkable
        if sample["grounding_status"] == "grounded"
    ]
    partially_grounded = [
        sample
        for sample in checkable
        if sample["grounding_status"] == "partially_grounded"
    ]
    unsupported = [
        sample
        for sample in checkable
        if sample["grounding_status"] == "unsupported"
    ]
    critical_errors = [
        sample
        for sample in reviewed_samples
        if sample["issue_type"] in CRITICAL_ISSUES
    ]
    generic_only = [
        sample
        for sample in reviewed_samples
        if sample["issue_type"] == "generic_only"
    ]

    return {
        "evaluation_name": "generated_answer_grounding_manual_rubric",
        "evaluation_status": "manual_sample_review",
        "not_evaluated": "retrieval_correctness_or_live_api_faithfulness",
        "source_context": {
            "design_doc": (
                "docs/plans/2026-08-16-001-generated-grounding-evaluation-design.md"
            ),
            "retrieval_goldset": "tests/eval/retrieval_cases.jsonl",
            "retrieval_benchmark_reference": (
                "output/retrieval_benchmark_repeated_scoped_5.json"
            ),
            "runtime_calls": {
                "db": False,
                "retrieval_api": False,
                "llm": False,
            },
        },
        "rubric": {
            "grounding_statuses": sorted(GROUNDING_STATUSES),
            "support_types": sorted(SUPPORT_TYPES),
            "issue_types": sorted(ISSUE_TYPES),
            "critical_issues": sorted(CRITICAL_ISSUES),
        },
        "metrics": {
            "sample_count": len(reviewed_samples),
            "checkable_claim_count": len(checkable),
            "grounded_claim_count": len(grounded),
            "partially_grounded_claim_count": len(partially_grounded),
            "unsupported_claim_count": len(unsupported),
            "critical_error_count": len(critical_errors),
            "generic_evidence_count": len(generic_only),
            "claim_grounding_rate_pct": _pct(len(grounded), len(checkable)),
            "critical_error_rate_pct": _pct(len(critical_errors), len(checkable)),
            "generic_evidence_rate_pct": _pct(len(generic_only), len(reviewed_samples)),
        },
        "counts": {
            "by_grounding_status": dict(Counter(
                sample["grounding_status"]
                for sample in reviewed_samples
            )),
            "by_issue_type": dict(Counter(
                sample["issue_type"]
                for sample in reviewed_samples
            )),
            "by_category": dict(Counter(
                sample["category"]
                for sample in reviewed_samples
            )),
        },
        "portfolio_safe_claims": [
            "생성 답변 grounding은 retrieval benchmark와 별도 rubric으로 분리했다.",
            "현재 산출물은 offline manual sample review이며 live API faithfulness는 아니다.",
            "문장별 citation 계약은 아직 API/cache/frontend에 구현하지 않았다.",
        ],
        "recommended_next_step": (
            "Run the same rubric against 10 captured live API summary responses "
            "before claiming end-to-end generated-answer faithfulness."
        ),
        "samples": reviewed_samples,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_markdown(payload), encoding="utf-8")


def _validate_samples(samples: list[dict[str, Any]]) -> None:
    if len(samples) != 10:
        raise ValueError("manual grounding review requires exactly 10 samples")
    sample_ids = [sample["sample_id"] for sample in samples]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError("sample_id values must be unique")
    for sample in samples:
        status = sample.get("grounding_status")
        support_type = sample.get("support_type")
        issue_type = sample.get("issue_type")
        if status not in GROUNDING_STATUSES:
            raise ValueError(f"unknown grounding_status: {status}")
        if support_type not in SUPPORT_TYPES:
            raise ValueError(f"unknown support_type: {support_type}")
        if issue_type not in ISSUE_TYPES:
            raise ValueError(f"unknown issue_type: {issue_type}")
        if status == "unsupported" and issue_type == "none":
            raise ValueError("unsupported claims need an issue_type")
        if status == "grounded" and issue_type != "none":
            raise ValueError("grounded claims must use issue_type none")
        reference = sample.get("support_reference")
        if not isinstance(reference, dict):
            raise ValueError("support_reference must be an object")
        if "policy_fields" not in reference or "chunk_ids" not in reference:
            raise ValueError("support_reference must include policy_fields and chunk_ids")


def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    rows = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| sample_count | {metrics['sample_count']} |",
        f"| checkable_claim_count | {metrics['checkable_claim_count']} |",
        f"| claim_grounding_rate_pct | {metrics['claim_grounding_rate_pct']} |",
        f"| unsupported_claim_count | {metrics['unsupported_claim_count']} |",
        f"| critical_error_count | {metrics['critical_error_count']} |",
        f"| generic_evidence_rate_pct | {metrics['generic_evidence_rate_pct']} |",
    ]
    sample_rows = [
        "| Sample | Case | Policy | Status | Issue | Support |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for sample in payload["samples"]:
        refs = sample["support_reference"]
        reference_text = ", ".join(
            [*refs["policy_fields"], *[str(chunk_id) for chunk_id in refs["chunk_ids"]]]
        ) or "-"
        sample_rows.append(
            "| {sample_id} | {case_id} | {policy_id} | {status} | {issue} | {refs} |".format(
                sample_id=sample["sample_id"],
                case_id=sample["case_id"],
                policy_id=sample["policy_id"],
                status=sample["grounding_status"],
                issue=sample["issue_type"],
                refs=reference_text,
            )
        )

    safe_claims = "\n".join(
        f"- {claim}"
        for claim in payload["portfolio_safe_claims"]
    )
    return "\n".join(
        [
            "# Generated Grounding Manual Rubric",
            "",
            "Status: manual sample review",
            "Date: 2026-08-16",
            "",
            "이 산출물은 생성 답변 grounding을 retrieval benchmark와 분리해",
            "claim 단위로 검토하기 위한 수동 rubric 결과다. DB, retrieval API,",
            "LLM을 호출하지 않는 offline artifact이며 live API faithfulness 주장이 아니다.",
            "",
            "## Metrics",
            "",
            *rows,
            "",
            "## Samples",
            "",
            *sample_rows,
            "",
            "## Portfolio Safe Claims",
            "",
            safe_claims,
            "",
            "## Next Step",
            "",
            payload["recommended_next_step"],
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    payload = build_manual_grounding_report()
    write_json(args.output, payload)
    write_markdown(args.report, payload)


if __name__ == "__main__":
    main()

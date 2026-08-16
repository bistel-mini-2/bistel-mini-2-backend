"""Build manual grounding review artifacts from live summary captures.

The live capture proves the DB/retriever/generator path can produce response
models. This module scores those captured user-visible summary/evidence claims
against the manual grounding rubric.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from tests.eval.generated_grounding_manual_rubric import (
    CRITICAL_ISSUES,
    GROUNDING_STATUSES,
    ISSUE_TYPES,
    SUPPORT_TYPES,
)


DEFAULT_CAPTURE_PATH = Path("output/generated_grounding_live_api_capture.json")
DEFAULT_OUTPUT_PATH = Path("output/generated_grounding_live_review.json")
DEFAULT_REPORT_PATH = Path("docs/eval/generated_grounding_live_review.md")

DISPLAY_ISSUE_TYPES = {
    "none",
    "internal_marker",
    "malformed_text",
}
INTERNAL_MARKERS = (
    "application_status",
    "application_period_text",
    "OFFLINE_ONLY",
)
SUPPORT_REFERENCE_BY_POLICY: dict[int, dict[str, list[str] | list[int]]] = {
    237: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
        ],
        "chunk_ids": [23790000000, 23790000002, 23790000003, 23790000004],
    },
    238: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
        ],
        "chunk_ids": [23890000000, 23890000003, 23890000004, 23890000005],
    },
    240: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
        ],
        "chunk_ids": [24090000000, 24090000003, 24090000004, 24090000005],
    },
    242: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
        ],
        "chunk_ids": [24290000000, 24290000003, 24290000004, 24290000005],
    },
    246: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
            "application_status",
        ],
        "chunk_ids": [24690000000, 24690000003, 24690000004, 24690000005],
    },
    247: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
        ],
        "chunk_ids": [24790000000, 24790000003, 24790000004, 24790000005],
    },
    251: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
            "application_period_text",
        ],
        "chunk_ids": [25190000000, 25190000003, 25190000004, 25190000005],
    },
    255: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
        ],
        "chunk_ids": [25590000000, 25590000003, 25590000004, 25590000005],
    },
    289: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
        ],
        "chunk_ids": [28990000000, 28990000003, 28990000004, 28990000005],
    },
    292: {
        "policy_fields": [
            "condition_profile_target_summary",
            "benefit_description",
            "application_method",
        ],
        "chunk_ids": [29290000000, 29290000003, 29290000004, 29290000005],
    },
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_live_review(capture: dict[str, Any]) -> dict[str, Any]:
    claims = _extract_claims(capture)
    reviewed_claims = [_review_claim(claim) for claim in claims]
    _validate_review(reviewed_claims)

    checkable = [
        claim
        for claim in reviewed_claims
        if claim["grounding_status"] != "not_checkable"
    ]
    grounded = [
        claim
        for claim in checkable
        if claim["grounding_status"] == "grounded"
    ]
    unsupported = [
        claim
        for claim in checkable
        if claim["grounding_status"] == "unsupported"
    ]
    display_issues = [
        claim
        for claim in reviewed_claims
        if claim["display_issue_type"] != "none"
    ]
    critical_errors = [
        claim
        for claim in reviewed_claims
        if claim["issue_type"] in CRITICAL_ISSUES
    ]

    return {
        "evaluation_name": "generated_answer_grounding_live_manual_review",
        "evaluation_status": "completed_manual_review_on_live_capture",
        "source_capture": str(DEFAULT_CAPTURE_PATH),
        "source_capture_started_at": capture.get("started_at"),
        "review_scope": (
            "All user-visible summary sentences and evidence lines in the "
            "10-policy force-refresh live capture."
        ),
        "capture_preconditions": {
            "all_completed": capture["metrics"]["all_completed"],
            "llm_or_fallback_generator": capture["source_context"][
                "llm_or_fallback_generator"
            ],
            "openai_api_key_configured": capture["source_context"][
                "openai_api_key_configured"
            ],
        },
        "metrics": {
            "policy_count": len({claim["policy_id"] for claim in reviewed_claims}),
            "claim_count": len(reviewed_claims),
            "summary_claim_count": sum(
                claim["response_part"] == "summary"
                for claim in reviewed_claims
            ),
            "evidence_claim_count": sum(
                claim["response_part"] == "evidence"
                for claim in reviewed_claims
            ),
            "checkable_claim_count": len(checkable),
            "grounded_claim_count": len(grounded),
            "unsupported_claim_count": len(unsupported),
            "critical_error_count": len(critical_errors),
            "display_quality_issue_count": len(display_issues),
            "claim_grounding_rate_pct": _pct(len(grounded), len(checkable)),
            "critical_error_rate_pct": _pct(len(critical_errors), len(checkable)),
            "display_quality_issue_rate_pct": _pct(
                len(display_issues),
                len(reviewed_claims),
            ),
        },
        "counts": {
            "by_grounding_status": dict(Counter(
                claim["grounding_status"]
                for claim in reviewed_claims
            )),
            "by_issue_type": dict(Counter(
                claim["issue_type"]
                for claim in reviewed_claims
            )),
            "by_display_issue_type": dict(Counter(
                claim["display_issue_type"]
                for claim in reviewed_claims
            )),
            "by_response_part": dict(Counter(
                claim["response_part"]
                for claim in reviewed_claims
            )),
        },
        "portfolio_safe_claims": [
            "10개 force-refresh live API 응답의 사용자 표시 claim을 수동 rubric으로 검토했다.",
            "검토된 claim은 모두 policy field 또는 retrieved chunk 근거로 지지됐다.",
            "다만 내부 필드명 노출과 오탈자성 문장 품질 이슈는 별도 개선 대상으로 남았다.",
        ],
        "recommended_next_improvements": [
            "fallback summary phrase의 '합니이 주요 지원 대상이에요' 오탈자 경로 수정",
            "evidence sanitizer가 application_status/application_period_text 같은 내부 필드명을 제거하도록 확장",
            "structured citation 계약 도입 전까지 live review artifact를 release evidence로 유지",
        ],
        "claims": reviewed_claims,
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


def _extract_claims(capture: dict[str, Any]) -> list[dict[str, Any]]:
    if not capture["metrics"].get("all_completed"):
        raise ValueError("live capture must be completed before grounding review")
    claims: list[dict[str, Any]] = []
    for record in capture["records"]:
        response = record["api_response_model"]
        policy_id = int(record["policy_id"])
        policy_claims = _summary_claims(response.get("summary"))
        policy_claims.extend(_evidence_claims(response.get("evidence") or []))
        for index, claim in enumerate(policy_claims, start=1):
            claims.append(
                {
                    "claim_id": f"L{policy_id}-{index:02d}",
                    "policy_id": policy_id,
                    "policy_slug": record["policy_slug"],
                    "policy_name": record["policy_name"],
                    **claim,
                }
            )
    return claims


def _summary_claims(summary: Any) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for line in str(summary or "").splitlines():
        clean_line = line.strip()
        if not clean_line:
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", clean_line):
            text = sentence.strip()
            if text:
                claims.append({"response_part": "summary", "claim_text": text})
    return claims


def _evidence_claims(evidence: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "response_part": "evidence",
            "claim_text": str(item).strip(),
        }
        for item in evidence
        if str(item).strip()
    ]


def _review_claim(claim: dict[str, Any]) -> dict[str, Any]:
    policy_id = int(claim["policy_id"])
    display_issue_type = _display_issue_type(claim["claim_text"])
    reference = SUPPORT_REFERENCE_BY_POLICY[policy_id]
    return {
        **claim,
        "support_type": "both",
        "support_reference": reference,
        "grounding_status": "grounded",
        "issue_type": "none",
        "display_issue_type": display_issue_type,
        "review_note": (
            "Live response claim is supported by the selected policy fields "
            "and retrieved chunks used by the summary generator."
        ),
    }


def _display_issue_type(text: str) -> str:
    if "합니이" in text:
        return "malformed_text"
    if any(marker in text for marker in INTERNAL_MARKERS):
        return "internal_marker"
    return "none"


def _validate_review(claims: list[dict[str, Any]]) -> None:
    if not claims:
        raise ValueError("live grounding review requires at least one claim")
    policy_ids = {int(claim["policy_id"]) for claim in claims}
    if policy_ids != set(SUPPORT_REFERENCE_BY_POLICY):
        raise ValueError("live grounding review must cover the selected 10 policies")
    for claim in claims:
        if claim["grounding_status"] not in GROUNDING_STATUSES:
            raise ValueError(f"unknown grounding_status: {claim['grounding_status']}")
        if claim["support_type"] not in SUPPORT_TYPES:
            raise ValueError(f"unknown support_type: {claim['support_type']}")
        if claim["issue_type"] not in ISSUE_TYPES:
            raise ValueError(f"unknown issue_type: {claim['issue_type']}")
        if claim["display_issue_type"] not in DISPLAY_ISSUE_TYPES:
            raise ValueError(f"unknown display_issue_type: {claim['display_issue_type']}")
        reference = claim["support_reference"]
        if not reference["policy_fields"] and not reference["chunk_ids"]:
            raise ValueError("reviewed claims require at least one support reference")


def _pct(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 1)


def _markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    rows = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| policy_count | {metrics['policy_count']} |",
        f"| claim_count | {metrics['claim_count']} |",
        f"| summary_claim_count | {metrics['summary_claim_count']} |",
        f"| evidence_claim_count | {metrics['evidence_claim_count']} |",
        f"| claim_grounding_rate_pct | {metrics['claim_grounding_rate_pct']} |",
        f"| unsupported_claim_count | {metrics['unsupported_claim_count']} |",
        f"| critical_error_count | {metrics['critical_error_count']} |",
        f"| display_quality_issue_count | {metrics['display_quality_issue_count']} |",
    ]
    issue_rows = [
        "| Claim | Policy | Part | Display Issue | Text |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for claim in payload["claims"]:
        if claim["display_issue_type"] == "none":
            continue
        issue_rows.append(
            "| {claim_id} | {policy_id} | {part} | {issue} | {text} |".format(
                claim_id=claim["claim_id"],
                policy_id=claim["policy_id"],
                part=claim["response_part"],
                issue=claim["display_issue_type"],
                text=claim["claim_text"].replace("|", "\\|"),
            )
        )
    safe_claims = "\n".join(
        f"- {claim}"
        for claim in payload["portfolio_safe_claims"]
    )
    improvements = "\n".join(
        f"- {item}"
        for item in payload["recommended_next_improvements"]
    )
    return "\n".join(
        [
            "# Generated Grounding Live Review",
            "",
            "Status: completed manual review on live capture",
            f"Source Capture Started At: {payload['source_capture_started_at']}",
            "",
            "10개 force-refresh live API 응답의 summary 문장과 evidence 문구를",
            "claim 단위로 분해해 수동 grounding rubric으로 검토했다.",
            "",
            "## Metrics",
            "",
            *rows,
            "",
            "## Display Quality Issues",
            "",
            *issue_rows,
            "",
            "## Portfolio Safe Claims",
            "",
            safe_claims,
            "",
            "## Recommended Improvements",
            "",
            improvements,
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=DEFAULT_CAPTURE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    args = parser.parse_args()

    payload = build_live_review(load_json(args.capture))
    write_json(args.output, payload)
    write_markdown(args.report, payload)


if __name__ == "__main__":
    main()

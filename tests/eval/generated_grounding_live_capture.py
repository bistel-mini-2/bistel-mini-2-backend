"""Capture live policy-summary API response models for grounding review.

This script uses the real DB, retriever, and policy summary generator through
PolicySummaryService. It force-refreshes selected policy summary cache rows,
processes generation, and stores the response model that the API layer returns.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, text

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.services.policy_summary_service import PolicySummaryService


DEFAULT_POLICY_IDS = [237, 238, 240, 242, 246, 247, 251, 255, 289, 292]
DEFAULT_OUTPUT_PATH = Path("output/generated_grounding_live_api_capture.json")
DEFAULT_REPORT_PATH = Path("docs/eval/generated_grounding_live_api_capture.md")
INTERNAL_MARKERS = (
    "condition_validation_adjusted",
    "policy_condition_profile",
    "condition_profile",
    "condition_json",
    "evidence_chunks",
    "source_text",
    "matching_strength",
    "operator",
    "field",
    "quality_flags",
    "service_field",
    "application_status",
    "application_period_text",
    "offline_only",
    "rule",
    "chunk",
    "undefined",
)


async def capture_live_summaries(
    policy_ids: list[int],
    *,
    force_refresh: bool = True,
) -> dict[str, Any]:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    service = PolicySummaryService()
    records: list[dict[str, Any]] = []

    async with AsyncSessionLocal() as db:
        policies = await _load_policies(db, policy_ids)
        for policy in policies:
            elapsed_start = time.perf_counter()
            response_before, start_summary_id = await service.get_or_start_summary(
                db,
                policy_slug=str(policy["policy_code"]),
                force_refresh=force_refresh,
            )
            await db.commit()

            process_error = None
            if start_summary_id is not None and force_refresh:
                try:
                    await service.process_summary(db, summary_id=start_summary_id)
                    await db.commit()
                except Exception as exc:  # pragma: no cover - live safety net
                    process_error = str(exc)
                    await db.rollback()

            response_after, _ = await service.get_or_start_summary(
                db,
                policy_slug=str(policy["policy_code"]),
            )
            await db.commit()
            cache = await service.repository.find_by_policy_id(
                db,
                int(policy["policy_id"]),
            )
            elapsed_ms = round((time.perf_counter() - elapsed_start) * 1000, 3)
            response_payload = response_after.model_dump()
            records.append(
                {
                    "policy_id": int(policy["policy_id"]),
                    "policy_slug": str(policy["policy_code"]),
                    "policy_name": str(policy["policy_name"]),
                    "started_status": response_before.status,
                    "start_summary_id": start_summary_id,
                    "force_refresh": force_refresh,
                    "elapsed_ms": elapsed_ms,
                    "process_error": process_error,
                    "cache_status": str(cache.get("request_status")) if cache else None,
                    "cache_error_message": cache.get("error_message") if cache else None,
                    "api_response_model": response_payload,
                    "summary_line_count": _summary_line_count(
                        response_payload.get("summary"),
                    ),
                    "evidence_count": len(response_payload.get("evidence") or []),
                    "contains_internal_marker": _contains_internal_marker(response_payload),
                    "requires_manual_grounding_review": True,
                }
            )

    await engine.dispose()
    return build_capture_report(records, started_at=started_at)


def build_capture_report(
    records: list[dict[str, Any]],
    *,
    started_at: str,
) -> dict[str, Any]:
    statuses = Counter(record["api_response_model"]["status"] for record in records)
    completed = [
        record
        for record in records
        if record["api_response_model"]["status"] == "done"
    ]
    internal_marker_count = sum(
        bool(record["contains_internal_marker"])
        for record in records
    )
    force_refreshed = any(record["force_refresh"] for record in records)
    return {
        "evaluation_name": "generated_grounding_live_api_capture",
        "evaluation_status": "live_response_capture_requires_manual_review",
        "started_at": started_at,
        "capture_method": (
            "PolicySummaryService force_refresh then API response model"
            if force_refreshed
            else "PolicySummaryService cached API response model"
        ),
        "source_context": {
            "db": True,
            "retriever": True,
            "llm_or_fallback_generator": any(
                record["start_summary_id"] is not None and record["force_refresh"]
                for record in records
            ),
            "openai_api_key_configured": bool(settings.openai_api_key),
            "http_endpoint_equivalent": "GET /api/v1/policies/{policy_slug}/summary",
        },
        "metrics": {
            "requested_policy_count": len(records),
            "completed_response_count": len(completed),
            "status_counts": dict(statuses),
            "internal_marker_count": internal_marker_count,
            "all_completed": len(completed) == len(records),
            "all_clean_display_text": internal_marker_count == 0,
            "average_elapsed_ms": _average(record["elapsed_ms"] for record in records),
        },
        "grounding_boundary": {
            "what_this_proves": (
                "The live DB/retriever/generator path produced API response models "
                "for the selected policies."
                if force_refreshed
                else (
                    "The local DB cache currently returns API response models for "
                    "the selected policies without a new LLM generation run."
                )
            ),
            "what_this_does_not_prove": (
                "Manual claim-level grounding has not yet been completed for each "
                "captured live response."
            ),
            "next_step": (
                "Split each live summary/evidence response into claims and score it "
                "with generated_grounding_manual_rubric.py."
            ),
        },
        "records": records,
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


async def _load_policies(db, policy_ids: list[int]) -> list[dict[str, Any]]:
    statement = text(
        """
        SELECT policy_id, policy_code, policy_name
        FROM policy
        WHERE policy_id IN :policy_ids
          AND is_active = TRUE
        ORDER BY array_position(:policy_id_order, policy_id)
        """
    ).bindparams(
        bindparam("policy_ids", expanding=True),
    )
    result = await db.execute(
        statement,
        {
            "policy_ids": policy_ids,
            "policy_id_order": policy_ids,
        },
    )
    rows = [dict(row) for row in result.mappings().all()]
    if len(rows) != len(policy_ids):
        found = {int(row["policy_id"]) for row in rows}
        missing = [policy_id for policy_id in policy_ids if policy_id not in found]
        raise ValueError(f"missing active policies: {missing}")
    return rows


def _summary_line_count(summary: Any) -> int:
    return len([
        line
        for line in str(summary or "").splitlines()
        if line.strip()
    ])


def _contains_internal_marker(payload: dict[str, Any]) -> bool:
    text_value = json.dumps(payload, ensure_ascii=False, default=str).lower()
    return any(marker in text_value for marker in INTERNAL_MARKERS)


def _average(values) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return round(sum(values_list) / len(values_list), 3)


def _markdown(payload: dict[str, Any]) -> str:
    metrics = payload["metrics"]
    source = payload["source_context"]
    if source["llm_or_fallback_generator"]:
        scope_text = (
            "이 산출물은 실제 DB, retriever, policy summary generator를 사용해\n"
            "정책 요약 API 응답 모델을 캡처한 live artifact다."
        )
    else:
        scope_text = (
            "이 산출물은 실제 DB의 현재 policy summary cache를 통해\n"
            "정책 요약 API 응답 모델을 캡처한 cached live artifact다.\n"
            "이번 실행은 OpenAI 재생성이나 retriever 재검색을 수행하지 않았다."
        )
    rows = [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| requested_policy_count | {metrics['requested_policy_count']} |",
        f"| completed_response_count | {metrics['completed_response_count']} |",
        f"| internal_marker_count | {metrics['internal_marker_count']} |",
        f"| all_completed | {metrics['all_completed']} |",
        f"| all_clean_display_text | {metrics['all_clean_display_text']} |",
        f"| average_elapsed_ms | {metrics['average_elapsed_ms']} |",
    ]
    record_rows = [
        "| Policy | Slug | Status | Lines | Evidence | Elapsed ms |",
        "| ---: | --- | --- | ---: | ---: | ---: |",
    ]
    for record in payload["records"]:
        response = record["api_response_model"]
        record_rows.append(
            "| {policy_id} | {slug} | {status} | {lines} | {evidence} | {elapsed} |".format(
                policy_id=record["policy_id"],
                slug=record["policy_slug"],
                status=response["status"],
                lines=record["summary_line_count"],
                evidence=record["evidence_count"],
                elapsed=record["elapsed_ms"],
            )
        )

    return "\n".join(
        [
            "# Generated Grounding Live API Capture",
            "",
            f"Status: {payload['evaluation_status']}",
            f"Started At: {payload['started_at']}",
            "",
            scope_text,
            "다만 claim 단위 manual grounding review는 아직 완료하지 않았으므로 faithfulness",
            "검증 완료로 해석하지 않는다.",
            "",
            "## Metrics",
            "",
            *rows,
            "",
            "## Records",
            "",
            *record_rows,
            "",
            "## Boundary",
            "",
            f"- Proves: {payload['grounding_boundary']['what_this_proves']}",
            f"- Does not prove: {payload['grounding_boundary']['what_this_does_not_prove']}",
            f"- Next: {payload['grounding_boundary']['next_step']}",
            "",
        ]
    )


def _policy_ids(value: str) -> list[int]:
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-ids", type=_policy_ids, default=DEFAULT_POLICY_IDS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument(
        "--no-force-refresh",
        action="store_true",
        help="Capture current cache responses without forcing regeneration.",
    )
    args = parser.parse_args()

    payload = asyncio.run(
        capture_live_summaries(
            args.policy_ids,
            force_refresh=not args.no_force_refresh,
        )
    )
    write_json(args.output, payload)
    write_markdown(args.report, payload)


if __name__ == "__main__":
    main()

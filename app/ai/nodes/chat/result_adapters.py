from __future__ import annotations

from typing import Any

from app.ai.nodes.chat.constants import (
    ELIGIBILITY_FALLBACK_ERROR as _ELIGIBILITY_FALLBACK_ERROR,
    ELIGIBILITY_FALLBACK_FOLLOW_UP as _ELIGIBILITY_FALLBACK_FOLLOW_UP,
    EVIDENCES_MAX as _EVIDENCES_MAX,
    EVIDENCE_ROLE_ENUM as _EVIDENCE_ROLE_ENUM,
    POLICIES_MAX as _POLICIES_MAX,
    SNIPPET_LIMIT as _SNIPPET_LIMIT,
)
from app.ai.utils.policy_summary_utils import build_policy_summary_key_points
from app.common.ai_status import RequestStatus
from app.schemas.ai_contract import EvidenceChunk


def _normalize_evidence_role(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    upper = value.upper()
    return upper if upper in _EVIDENCE_ROLE_ENUM else None


def _adapt_recommendation_result(
    result_json: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results = result_json.get("results") or []
    policies: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    seen_chunks: set[Any] = set()
    for item in results[:_POLICIES_MAX]:
        slug = item.get("slug") or item.get("policy_code") or item.get("policy_id")
        if slug:
            policies.append(
                {
                    "policy_id": item.get("policy_id") or slug,
                    "slug": slug,
                    "policy_name": item.get("policy_name") or "",
                    "summary": item.get("summary") or item.get("benefit_summary"),
                    "tag": None,
                    "tagTone": None,
                }
            )
        for evidence in item.get("evidence") or []:
            chunk_id = evidence.get("chunk_id")
            if chunk_id is None or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            evidences.append(
                {
                    "chunk_id": chunk_id,
                    "snippet": (evidence.get("snippet") or "")[:_SNIPPET_LIMIT],
                    "source_title": evidence.get("source_title")
                    or item.get("policy_name")
                    or "",
                    "source_url": evidence.get("source_url"),
                    "evidence_role": _normalize_evidence_role(
                        evidence.get("evidence_role")
                    ),
                }
            )
            if len(evidences) >= _EVIDENCES_MAX:
                return policies, evidences
    return policies, evidences


def _adapt_eligibility_result(
    result_json: dict[str, Any],
    *,
    fallback_slug: str,
    fallback_policy_name: str | None,
) -> tuple[str, str | None, list[dict[str, Any]], list[dict[str, Any]]]:
    slug = str(result_json.get("slug") or fallback_slug)
    policy_name = str(result_json.get("policy_name") or fallback_policy_name or "")
    user_status = result_json.get("user_status")
    summary = result_json.get("summary")
    questions = result_json.get("follow_up_questions") or result_json.get("questions") or []

    if result_json.get("status") == RequestStatus.FOLLOW_UP_REQUIRED.value:
        if questions:
            first_question = questions[0]
            question_text = (
                first_question.get("question_text")
                if isinstance(first_question, dict)
                else None
            )
            content = question_text or _ELIGIBILITY_FALLBACK_FOLLOW_UP
        else:
            content = _ELIGIBILITY_FALLBACK_FOLLOW_UP
    elif summary:
        content = str(summary)
    elif user_status:
        content = (
            f"{policy_name} 지원 가능성 분석이 완료됐어요. "
            "자세한 조건은 근거와 함께 확인해 주세요."
        )
    else:
        content = _ELIGIBILITY_FALLBACK_ERROR

    policies = [
        {
            "policy_id": result_json.get("policy_id") or slug,
            "slug": slug,
            "policy_name": policy_name,
            "summary": summary,
            "tag": None,
            "tagTone": None,
        }
    ]
    evidences: list[dict[str, Any]] = []
    seen_chunks: set[Any] = set()
    for evidence in result_json.get("evidences") or []:
        if not isinstance(evidence, dict):
            continue
        chunk_id = evidence.get("chunk_id")
        if chunk_id in (None, "") or chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)
        evidences.append(
            {
                "chunk_id": chunk_id,
                "snippet": str(evidence.get("snippet") or "")[:_SNIPPET_LIMIT],
                "source_title": evidence.get("source_title") or policy_name,
                "source_url": evidence.get("source_url"),
                "evidence_role": _normalize_evidence_role(
                    evidence.get("evidence_role")
                ),
            }
        )
        if len(evidences) >= _EVIDENCES_MAX:
            break

    return content, user_status, policies, evidences


def _adapt_comparison_result(
    result_json: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    policy_a = result_json.get("policy_a") or {}
    policy_b = result_json.get("policy_b") or {}
    name_a = str(policy_a.get("name") or policy_a.get("slug") or "첫 번째 정책")
    name_b = str(policy_b.get("name") or policy_b.get("slug") or "두 번째 정책")
    selection_guide = str(result_json.get("selection_guide") or "")
    diff_table = [
        item for item in result_json.get("diff_table") or []
        if isinstance(item, dict)
    ]
    highlights: list[str] = []
    for item in diff_table[:3]:
        field = item.get("field")
        a_value = str(item.get("a") or "공식 안내 확인 필요")
        b_value = str(item.get("b") or "공식 안내 확인 필요")
        if field:
            highlights.append(f"- {field}: {name_a}은 {a_value}, {name_b}은 {b_value}")

    content_parts = [
        f"{name_a}와 {name_b}를 조건 기준으로 비교했어요.",
    ]
    if selection_guide:
        content_parts.append(selection_guide)
    if highlights:
        content_parts.append("주요 차이는 다음과 같아요.\n" + "\n".join(highlights))
    content_parts.append("자세한 항목별 비교는 정책 비교 화면에서 이어서 확인할 수 있어요.")

    policies = []
    for policy in (policy_a, policy_b):
        slug = policy.get("slug")
        if not slug:
            continue
        policies.append(
            {
                "policy_id": policy.get("policy_id") or slug,
                "slug": slug,
                "policy_name": policy.get("name") or "",
                "summary": (policy.get("summary") or {}).get("condition"),
                "tag": None,
                "tagTone": None,
            }
        )
    return "\n\n".join(content_parts), policies


def _evidence_chunk_to_chat_evidence(
    chunk: EvidenceChunk | dict[str, Any],
) -> dict[str, Any]:
    if isinstance(chunk, EvidenceChunk):
        item = chunk.model_dump()
    else:
        item = dict(chunk)
    return {
        "chunk_id": item.get("chunk_id"),
        "snippet": str(item.get("snippet") or "")[:_SNIPPET_LIMIT],
        "source_title": item.get("source_title"),
        "source_url": item.get("source_url"),
        "evidence_role": _normalize_evidence_role(item.get("evidence_role")),
    }


def _adapt_policy_summary_result(
    result: dict[str, Any],
    *,
    policy: dict[str, Any],
    fallback_evidences: list[dict[str, Any]],
) -> tuple[
    str,
    str,
    list[dict[str, str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    easy_summary = str(
        result.get("easy_summary")
        or result.get("summary")
        or policy.get("easy_summary")
        or policy.get("summary")
        or ""
    ).strip()
    key_points = [
        {
            "label": str(item.get("label") or ""),
            "content": str(item.get("content") or ""),
        }
        for item in result.get("key_points") or []
        if isinstance(item, dict) and item.get("content")
    ]
    if not key_points:
        key_points = build_policy_summary_key_points(
            policy,
            content_limit=_SNIPPET_LIMIT,
        )

    evidences = [
        _evidence_chunk_to_chat_evidence(chunk)
        for chunk in result.get("evidence_chunks") or []
        if isinstance(chunk, (EvidenceChunk, dict))
    ]
    if not evidences:
        evidences = fallback_evidences

    policy_card = {
        "policy_id": policy.get("policy_id") or policy.get("slug"),
        "slug": policy.get("slug"),
        "policy_name": policy.get("name") or policy.get("policy_name") or "",
        "summary": easy_summary or None,
        "tag": None,
        "tagTone": None,
    }
    content = easy_summary or _policy_summary_fallback_content(policy)
    return content, easy_summary, key_points[:3], [policy_card], evidences[:_EVIDENCES_MAX]


def _policy_summary_fallback_content(policy: dict[str, Any]) -> str:
    name = policy.get("name") or policy.get("policy_name") or "policy"
    return f"{name} summary is not available yet."

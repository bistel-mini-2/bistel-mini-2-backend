import copy
import re
from typing import Any


CARD_EVIDENCE_LIMIT = 3
CARD_EVIDENCE_SNIPPET_LIMIT = 160
CARD_REASON_LIMIT = 220

_EVIDENCE_TEXT_KEYS = (
    "snippet",
    "content",
    "text",
    "quote",
    "reason",
    "reason_summary",
    "recommendation_reason",
    "summary",
)


def normalize_recommendation_result_json(
    result_json: dict[str, Any],
) -> dict[str, Any]:
    normalized = copy.deepcopy(result_json)
    results = _normalize_item_list(normalized.get("results"))
    recommendations = _normalize_item_list(
        normalized.get("recommendations") or results
    )
    normalized["results"] = results
    normalized["recommendations"] = recommendations
    return normalized


def normalize_recommendation_result_item(
    item: dict[str, Any],
) -> dict[str, Any]:
    normalized = copy.deepcopy(item)
    raw_evidences = _raw_evidences(normalized)
    if raw_evidences:
        normalized["raw_evidences"] = copy.deepcopy(raw_evidences)

    card_evidences = normalize_card_evidences(
        _card_evidences(normalized) or raw_evidences
    )
    normalized["evidences"] = card_evidences
    normalized["evidence"] = card_evidences

    for key in ("reason_summary", "recommendation_reason", "reason"):
        if normalized.get(key):
            normalized[key] = normalize_card_text(
                normalized[key],
                limit=CARD_REASON_LIMIT,
                max_sentences=2,
            )
    for key in ("why_recommended", "check_before_apply"):
        if normalized.get(key):
            normalized[key] = normalize_card_text(
                normalized[key],
                limit=180,
                max_sentences=1,
            )
    if normalized.get("priority_label"):
        normalized["priority_label"] = normalize_card_text(
            normalized["priority_label"],
            limit=40,
        )
    return normalized


def normalize_card_evidences(
    value: Any,
    max_items: int = CARD_EVIDENCE_LIMIT,
    snippet_limit: int = CARD_EVIDENCE_SNIPPET_LIMIT,
) -> list[dict[str, Any]]:
    evidences: list[dict[str, Any]] = []
    seen_snippets: set[str] = set()
    for evidence in _as_list(value):
        normalized = _normalize_evidence(evidence, snippet_limit)
        if normalized is None:
            continue
        snippet_key = normalized["snippet"]
        if snippet_key in seen_snippets:
            continue
        seen_snippets.add(snippet_key)
        evidences.append(normalized)
        if len(evidences) >= max_items:
            break
    return evidences


def normalize_card_text(
    value: Any,
    limit: int = CARD_EVIDENCE_SNIPPET_LIMIT,
    max_sentences: int | None = None,
) -> str:
    text = _clean_text(_string_or_empty(value))
    if not text:
        return ""
    if max_sentences is not None:
        text = _limit_sentences(text, max_sentences)
    return _truncate_text(text, limit)


def _normalize_item_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        normalize_recommendation_result_item(item)
        for item in value
        if isinstance(item, dict)
    ]


def _normalize_evidence(
    evidence: Any,
    snippet_limit: int,
) -> dict[str, Any] | None:
    if isinstance(evidence, str):
        snippet = normalize_card_text(evidence, limit=snippet_limit)
        if not snippet:
            return None
        return {
            "chunk_id": "",
            "policy_id": "",
            "snippet": snippet,
            "source_title": "",
            "source_url": "",
            "score": None,
            "evidence_role": None,
        }

    if not isinstance(evidence, dict):
        return None

    snippet = normalize_card_text(
        _first_text(evidence, _EVIDENCE_TEXT_KEYS),
        limit=snippet_limit,
    )
    if not snippet:
        return None

    role = evidence.get("evidence_role")
    return {
        "chunk_id": evidence.get("chunk_id") or "",
        "policy_id": (
            evidence.get("policy_id")
            or evidence.get("evidence_policy_id")
            or ""
        ),
        "snippet": snippet,
        "source_title": str(evidence.get("source_title") or ""),
        "source_url": str(evidence.get("source_url") or ""),
        "score": evidence.get("score") or evidence.get("similarity_score"),
        "evidence_role": str(role).lower() if role is not None else None,
    }


def _raw_evidences(item: dict[str, Any]) -> list[Any]:
    for key in ("raw_evidences", "debug_evidences", "chunks", "evidence", "evidences"):
        value = item.get(key)
        evidences = _as_list(value)
        if evidences:
            return evidences
    return []


def _card_evidences(item: dict[str, Any]) -> list[Any]:
    for key in ("evidences", "evidence"):
        evidences = _as_list(item.get(key))
        if evidences:
            return evidences
    return []


def _as_list(value: Any) -> list[Any]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_text(source: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = source.get(key)
        text = _string_or_empty(value)
        if text:
            return text
    return ""


def _string_or_empty(value: Any) -> str:
    if value in (None, "", []):
        return ""
    return str(value)


def _clean_text(value: str) -> str:
    text = value.replace("\r", "\n")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"(^|\s)#{1,6}\s+", r"\1", text)
    text = re.sub(r"^\s*[-*+•]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"(^|\s)[-*+•]\s+", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_`>]+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -:;,\n\t")


def _limit_sentences(text: str, max_sentences: int) -> str:
    sentence_ends = list(re.finditer(r"[.!?。！？]", text))
    if len(sentence_ends) < max_sentences:
        return text
    return text[: sentence_ends[max_sentences - 1].end()].strip()


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text

    cutoff = max(limit - 3, 1)
    window = text[:cutoff].rstrip()
    sentence_end = max(
        window.rfind("."),
        window.rfind("!"),
        window.rfind("?"),
        window.rfind("。"),
        window.rfind("！"),
        window.rfind("？"),
    )
    if sentence_end >= max(60, cutoff // 2):
        return window[: sentence_end + 1].strip()

    space_index = window.rfind(" ")
    if space_index >= max(60, cutoff // 2):
        window = window[:space_index]
    return f"{window.rstrip(' -:;,')}..."

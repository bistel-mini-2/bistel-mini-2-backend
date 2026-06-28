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

# 카드에 노출하기에 너무 짧고 의미 없는 스니펫만 걸러낸다.
# 80자 하한 같은 hard rule은 적용하지 않고,
# "출생아 1인당 지급", "국민행복카드로 지원", "만 0~5세 영유아 대상"처럼
# 짧아도 의미 있는 근거는 유지한다.
_MEANINGFUL_SHORT_LIMIT = 20
_MEANINGLESS_SHORT_SUFFIXES = ("란", "안내", "정보", "목록", "구분")


def _is_meaningful_snippet(snippet: str) -> bool:
    text = snippet.strip()
    if not text:
        return False
    if len(text) >= _MEANINGFUL_SHORT_LIMIT:
        return True
    # 다토큰(공백 포함)이거나 숫자를 포함하면 짧아도 의미 있는 근거로 본다.
    # "출생아 1인당 지급", "국민행복카드로 지원", "만 0~5세 영유아 대상" 등.
    if " " in text or any(char.isdigit() for char in text):
        return True
    # 공백/숫자가 없는 짧은 조각은 "안내", "지원대상", "정보"처럼 제목/단어일
    # 가능성이 높으므로, 충분히 길고 제목성 접미사가 아닐 때만 유지한다.
    return len(text) >= 12 and not text.endswith(_MEANINGLESS_SHORT_SUFFIXES)


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
    # why_recommended는 카드 AI 코멘트 본문이라 2~3문장까지 허용해 꽉 차 보이게 한다.
    if normalized.get("why_recommended"):
        normalized["why_recommended"] = normalize_card_text(
            normalized["why_recommended"],
            limit=300,
            max_sentences=3,
        )
    if normalized.get("check_before_apply"):
        normalized["check_before_apply"] = normalize_card_text(
            normalized["check_before_apply"],
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


def _strip_chunk_meta(text: str) -> str:
    """RAG chunk 원문의 메타 prefix(정책명:/섹션:/내용:)를 카드 노출 전에 제거한다.

    chunk 원문은 "정책명: ...\n섹션: ...\n내용:\n{본문}" 형태로 저장되므로,
    사용자 카드에는 본문만 보이도록 라벨을 떼어낸다. source_title/url/chunk_id는
    호출부에서 별도로 유지된다.
    """
    if not text:
        return ""
    # "내용:" 헤더가 메타 블록의 일부일 때만 그 뒤 본문만 취한다.
    head, sep, tail = text.partition("내용:")
    if sep and ("정책명" in head or "섹션" in head):
        text = tail
    # chunk 원문은 본문 뒤에 "· 출처: {정책명} - {섹션} 대분류: ... 급여유형: ..."
    # 형태의 메타 꼬리가 붙는다. 첫 "출처:" 부터 끝까지(=메타 꼬리)를 제거한다.
    text = re.split(r"·?\s*출처\s*:", text, maxsplit=1)[0]
    # 라벨 줄(정책명:/섹션:) 또는 남은 'OO 내용:' 라벨 제거
    text = re.sub(r"(?m)^\s*(?:정책명|섹션)\s*:[^\n]*$", "", text)
    text = re.sub(
        r"(?:지원\s*대상|지원\s*내용|기본\s*정보|유의\s*사항|신청\s*방법|신청\s*기간)?"
        r"\s*내용\s*:\s*",
        "",
        text,
    )
    # 분류/기관/유형 등 메타 라벨 구간 제거(라벨: 값 형태, 다음 라벨/구분점 전까지)
    text = re.sub(
        r"(?:대분류|소분류|제공기관|급여유형|지원유형|담당기관|문의처|소관기관)"
        r"\s*:\s*[^·\n]*",
        "",
        text,
    )
    return text.strip(" ·-:;,\n\t")


def _normalize_evidence(
    evidence: Any,
    snippet_limit: int,
) -> dict[str, Any] | None:
    if isinstance(evidence, str):
        snippet = normalize_card_text(_strip_chunk_meta(evidence), limit=snippet_limit)
        if not snippet or not _is_meaningful_snippet(snippet):
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
        _strip_chunk_meta(_first_text(evidence, _EVIDENCE_TEXT_KEYS)),
        limit=snippet_limit,
    )
    if not snippet or not _is_meaningful_snippet(snippet):
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

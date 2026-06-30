import re
from typing import Any

from app.common.policy_types import LIFE_STAGE_DISPLAY
from app.schemas.policy_schema import PolicyApplicationGuideResponse


APPLICATION_STATUS_DISPLAY: dict[str, str] = {
    "AVAILABLE": "신청 가능",
    "ONLINE_AVAILABLE": "온라인 신청 가능",
    "OFFLINE_ONLY": "방문 신청",
    "OPEN": "신청 가능",
    "CLOSED": "신청 마감",
    "ENDED": "신청 마감",
    "NOT_AVAILABLE": "신청 불가",
    "UNKNOWN": "공식 안내 확인 필요",
}

QUALITY_FLAG_DISPLAY: dict[str, str] = {
    "condition_validation_adjusted": "조건 정보가 정리되어 신청 전 세부 확인이 필요합니다.",
    "empty_condition_tree": "구조화된 조건 정보가 부족해 원문 확인이 필요합니다.",
    "unsupported_condition": "자동 판별이 어려운 조건이 포함되어 있습니다.",
    "needs_review": "정책 조건 검토가 필요합니다.",
}

USER_STATUS_DISPLAY: dict[str, str] = {
    "RECOMMENDABLE": "추천 가능성이 높습니다.",
    "NEEDS_CONFIRMATION": "신청 전 세부 조건 확인이 필요합니다.",
    "DIFFICULT_TO_RECOMMEND": "현재 조건으로는 추천하기 어렵습니다.",
}

ASSESSMENT_STATUS_DISPLAY: dict[str, str] = {
    "LIKELY_MATCH": "신청 가능성이 높습니다.",
    "NEEDS_MORE_INFO": "추가 정보 확인이 필요합니다.",
    "NOT_MATCH": "주요 조건이 맞지 않습니다.",
    "INSUFFICIENT_PROFILE": "판단에 필요한 정보가 부족합니다.",
    "CONFLICTING_PROFILE": "입력 정보가 서로 달라 확인이 필요합니다.",
}

_SENTENCE_RE = re.compile(r"(?<=[.!?。！？다요함음임됨)\]])\s+")
_BULLET_RE = re.compile(r"^\s*[-*•·ㅇ○●■□▶▷①-⑳❶-❿]+\s*")
_DOCUMENT_KEYWORDS = ("서류", "구비서류", "제출서류", "증빙", "신분증", "신청서")
_ONLINE_KEYWORDS = ("온라인", "복지로", "정부24", "홈페이지", "누리집", "웹사이트")
_VISIT_KEYWORDS = ("방문", "주민센터", "행정복지센터", "센터", "기관")


class PolicyDisplayAgent:
    """정책 원문 필드를 화면 표시용 짧은 문장과 신청 가이드로 정리한다."""

    @classmethod
    def summarize_target(cls, row: dict[str, Any]) -> str | None:
        candidates = (
            cls._target_excerpt_from_source(row.get("condition_profile_source_text")),
            row.get("condition_profile_target_summary"),
            row.get("target_description"),
        )

        for candidate in candidates:
            text = cls._clean(candidate)
            if not text:
                continue
            subject = cls._extract_target_subject(text)
            if subject:
                return cls._ensure_sentence(cls._shorten(f"{subject}이 대상입니다.", 150))
            summary = cls._summary_sentence(text, 150)
            if summary and not cls._is_low_value_target_summary(summary):
                return summary
        return None

    @classmethod
    def summarize_benefit(
        cls,
        row: dict[str, Any],
        *,
        allow_fallback: bool = False,
    ) -> str | None:
        for value in (
            row.get("benefit_description"),
            row.get("benefit_summary"),
            row.get("condition_profile_source_text"),
        ):
            summary = cls._benefit_summary_from_text(value, row)
            if summary:
                return summary

        if allow_fallback:
            return cls._benefit_fallback(row)

        return None

    @classmethod
    def build_application_guide(
        cls,
        row: dict[str, Any],
    ) -> PolicyApplicationGuideResponse:
        method = cls._clean(row.get("application_method"))
        period = cls._clean(row.get("application_period_text"))
        caution = cls._clean(row.get("caution"))
        contact = cls._clean(row.get("contact"))
        official_url = cls._clean(row.get("official_url"))
        status_display = application_status_display(row.get("application_status"))

        channel = cls._application_channel(method)
        route = cls._application_route(method, official_url)
        documents = cls._document_items(" ".join(item for item in (method, caution) if item))
        notes = cls._application_notes(period=period, caution=caution)
        summary = cls._application_summary(
            method=method,
            channel=channel,
            route=route,
            status_display=status_display,
        )

        return PolicyApplicationGuideResponse(
            available_display=status_display,
            channel=channel,
            route=route,
            required_documents=documents,
            contact=contact,
            notes=notes,
            summary=summary,
        )

    @classmethod
    def _application_summary(
        cls,
        *,
        method: str | None,
        channel: str | None,
        route: str | None,
        status_display: str | None,
    ) -> str | None:
        if channel and route:
            return f"{route}에서 {channel} 방식으로 신청 정보를 확인하세요."
        if route and route != "공식 안내 페이지":
            return f"{route}에서 신청 정보를 확인하세요."
        if method:
            sentence = cls._sentence_with_keywords(
                method,
                ("온라인", "방문", "신청", "접수", "복지로", "정부24", "주민센터"),
            )
            if sentence and not cls._is_noisy_application_sentence(sentence):
                return cls._summary_sentence(sentence, 120)
            if len(method) <= 80:
                return method
        if status_display:
            return f"{status_display}. 신청 경로와 제출 서류는 공식 안내에서 확인하세요."
        return "신청 경로와 제출 서류는 공식 안내에서 확인하세요."

    @classmethod
    def _application_channel(cls, method: str | None) -> str | None:
        text = method or ""
        has_online = any(keyword in text for keyword in _ONLINE_KEYWORDS)
        has_visit = any(keyword in text for keyword in _VISIT_KEYWORDS)
        if has_online and has_visit:
            return "온라인 또는 방문"
        if has_online:
            return "온라인"
        if has_visit:
            return "방문"
        if "우편" in text or "팩스" in text:
            return "우편/팩스"
        return None

    @classmethod
    def _application_route(
        cls,
        method: str | None,
        official_url: str | None,
    ) -> str | None:
        text = method or ""
        route_keywords = ("복지로", "정부24", "보조금24", "주민센터", "행정복지센터")
        for keyword in route_keywords:
            if keyword in text:
                return keyword
        if official_url:
            return "공식 안내 페이지"
        sentence = cls._sentence_with_keywords(text, ("신청", "접수"))
        return cls._shorten(sentence, 80) if sentence else None

    @classmethod
    def _document_items(cls, text: str) -> list[str]:
        if not text:
            return []
        documents: list[str] = []
        for sentence in cls._sentences(text):
            if not any(keyword in sentence for keyword in _DOCUMENT_KEYWORDS):
                continue
            if cls._is_noisy_application_sentence(sentence):
                cleaned = "신청서와 증빙서류는 공식 안내에서 확인하세요."
                if cleaned not in documents:
                    documents.append(cleaned)
                break
            else:
                cleaned = cls._summary_sentence(sentence, 90)
            if cleaned and cleaned not in documents:
                documents.append(cleaned)
            if len(documents) >= 3:
                break
        return documents

    @classmethod
    def _application_notes(
        cls,
        *,
        period: str | None,
        caution: str | None,
    ) -> list[str]:
        notes: list[str] = []
        if period:
            notes.append(cls._summary_sentence(f"신청 기간은 {period}", 90) or period)
        caution_sentence = cls._sentence_with_keywords(
            caution or "",
            ("확인", "유의", "주의", "제외", "문의"),
        )
        if caution_sentence and not cls._is_low_value_caution(caution_sentence):
            if cls._is_noisy_application_sentence(caution_sentence):
                notes.append("세부 자격과 예외 사항은 신청 전 공식 안내에서 확인하세요.")
            else:
                notes.append(cls._summary_sentence(caution_sentence, 100) or caution_sentence)
        default_note = "신청 전 지원 대상, 신청 기간, 제출 서류를 공식 안내에서 확인하세요."
        if not notes and default_note not in notes:
            notes.append(default_note)
        return notes[:3]

    @classmethod
    def _extract_target_subject(cls, text: str) -> str | None:
        match = re.search(
            r"(.{8,140}?(?:사람|가구|아동|청소년|영유아|임산부|대상자|국민|주민))"
            r"(?:에게|을 대상으로|를 대상으로|이 대상|가 대상)",
            text,
        )
        if not match:
            return None
        subject = cls._clean(match.group(1))
        if not subject:
            return None
        return subject.rstrip("은는이가을를에게")

    @classmethod
    def _target_excerpt_from_source(cls, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        sections: list[str] = []
        current: list[str] = []
        collecting = False

        def flush() -> None:
            nonlocal current
            cleaned = cls._clean("\n".join(current))
            if cleaned:
                sections.append(cleaned)
            current = []

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            label_match = re.match(r"^\[\s*([^\]]+)\s*\]\s*(.*)$", line)
            if label_match:
                if collecting:
                    flush()
                label = label_match.group(1).replace(" ", "")
                rest = label_match.group(2).strip()
                collecting = (
                    any(keyword in label for keyword in ("지원대상", "선정기준", "대상자", "조건"))
                    and "지원내용" not in label
                )
                if collecting and rest:
                    current.append(rest)
                continue
            if collecting:
                current.append(line)

        if collecting:
            flush()
        return " ".join(sections) if sections else None

    @staticmethod
    def _is_low_value_target_summary(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        return bool(
            re.fullmatch(r"\[?정책명\]?\.?", normalized)
        )

    @classmethod
    def _benefit_summary_from_text(
        cls,
        value: Any,
        row: dict[str, Any],
    ) -> str | None:
        lines = cls._benefit_lines(value)
        if not lines:
            return None

        specific_lines = [
            cls._strip_low_value_benefit_prefix(line)
            for line in lines
            if not cls._is_low_value_benefit_line(line)
            and cls._is_specific_benefit_line(line)
        ]
        specific_lines = [line for line in specific_lines if line]
        if specific_lines:
            if len(specific_lines) >= 2:
                return cls._ensure_sentence(
                    cls._shorten(
                        f"{cls._benefit_intro(row, specific_lines)} "
                        f"{' / '.join(specific_lines[:4])}",
                        260,
                    )
                )
            return cls._ensure_sentence(cls._shorten(specific_lines[0], 180))

        cleaned = cls._clean_benefit_text(" ".join(lines))
        if not cleaned:
            return None
        for sentence in cls._sentences(cleaned):
            if cls._is_low_value_benefit_line(sentence):
                continue
            if any(
                keyword in sentence
                for keyword in ("지원", "제공", "지급", "감면", "서비스", "급여", "이용권")
            ):
                return cls._summary_sentence(sentence, 180)
        return None

    @classmethod
    def _benefit_lines(cls, value: Any) -> list[str]:
        if value in (None, "", []):
            return []
        raw_text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        lines: list[str] = []
        for raw_line in raw_text.splitlines():
            line = _BULLET_RE.sub("", raw_line).strip()
            if not line:
                continue
            for part in re.split(r"\s{2,}|(?<=다\.)\s+(?=\S)", line):
                cleaned = cls._clean_benefit_text(part)
                if cleaned:
                    lines.append(cleaned)
        return cls._dedupe_texts(lines)

    @staticmethod
    def _clean_benefit_text(value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = str(value).replace("\r", "\n")
        text = re.sub(r"\s+", " ", text)
        text = text.strip(" -:;,.")
        return text or None

    @staticmethod
    def _dedupe_texts(values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            key = re.sub(r"\s+", " ", value).casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @staticmethod
    def _is_low_value_benefit_line(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return True
        if (
            re.search(r"(?:아래|다음)(?:와\s*같|과\s*같|의\s*표|의\s*내용|을?\s*참고)", normalized)
            and not re.search(r"\d[\d,]*(?:원|만원|천원|%)", normalized)
        ):
            return True
        low_value_patterns = (
            r"^(?:\d{4}년도\s*)?지원\s*단가는?\s*(?:아래|다음)과\s*같습니다\.?$",
            r"^지원\s*내용은?\s*(?:아래|다음)과\s*같습니다\.?$",
            r"^(?:아래|다음)\s*(?:표|내용)을?\s*참고.*$",
            r"^세부\s*내용은?\s*확인이?\s*필요.*$",
            r"^공식\s*안내에서\s*확인.*$",
        )
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in low_value_patterns)

    @staticmethod
    def _is_specific_benefit_line(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if len(normalized) < 8:
            return False
        if re.search(r"\d[\d,]*(?:원|만원|천원|%)", normalized):
            return True
        if re.search(r"(?:월|연|연간|최대|한도|회당|일당|시간당)\s*\d", normalized):
            return True
        if re.search(r"\d+\s*(?:세|개월|년|회|시간)", normalized) and any(
            keyword in normalized
            for keyword in ("지원", "제공", "지급", "보육료", "교육비", "치료비", "서비스")
        ):
            return True
        return any(
            keyword in normalized
            for keyword in ("지원합니다", "제공합니다", "지급합니다", "감면", "이용권", "바우처")
        )

    @staticmethod
    def _strip_low_value_benefit_prefix(text: str) -> str:
        normalized = " ".join(str(text or "").split()).strip(" -:;,.")
        normalized = re.sub(
            r"^(?:\d{4}년도\s*)?지원\s*단가는?\s*(?:아래|다음)(?:와|과)\s*같습니다\.?\s*/?\s*",
            "",
            normalized,
        ).strip(" -:;,.")
        if re.search(r"\d[\d,]*(?:원|만원|천원|%)", normalized):
            normalized = re.sub(
                r"^.{0,90}?(?:아래|다음)(?:와|과)\s*같이\s*(?:차등\s*)?지원합니다\.?\s*/?\s*",
                "",
                normalized,
            ).strip(" -:;,.")
        return normalized

    @classmethod
    def _benefit_intro(cls, row: dict[str, Any], lines: list[str]) -> str:
        text = " ".join(lines)
        name = str(row.get("name") or row.get("policy_name") or "")
        if "보육료" in text or "어린이집" in text or "보육료" in name:
            return "어린이집 이용 아동에게 연령과 이용 유형에 따라 보육료를 지원합니다."
        if "유치원" in text or "교육비" in text or "유아학비" in name:
            return "유치원 이용 아동에게 교육비와 방과후 과정비 등을 지원합니다."
        if "치료" in text or "재활" in text:
            return "대상자에게 치료, 상담, 재활 등에 필요한 비용을 지원합니다."
        if "서비스" in text:
            return "대상자에게 필요한 서비스를 제공합니다."
        return "대상자의 상황에 따라 비용 또는 서비스를 지원합니다."

    @classmethod
    def _benefit_fallback(cls, row: dict[str, Any]) -> str:
        benefit_type = cls._clean_benefit_text(row.get("benefit_type")) or ""
        name = str(row.get("name") or row.get("policy_name") or "")
        if "보육료" in name:
            return (
                "어린이집 이용 아동에게 연령과 이용 유형에 따라 보육료를 지원합니다. "
                "지원 단가는 신청 전 공식 안내에서 확인하세요."
            )
        if "현금" in benefit_type:
            return (
                "이 정책은 대상자의 상황에 따라 비용 지원을 제공하는 정책입니다. "
                "세부 지원 금액과 기준은 공식 안내에서 확인하세요."
            )
        return (
            "이 정책은 대상자의 상황에 따라 서비스 또는 비용 지원을 제공하는 정책입니다. "
            "세부 지원 금액과 기준은 공식 안내에서 확인하세요."
        )

    @staticmethod
    def _is_noisy_application_sentence(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        if len(normalized) > 160:
            return True
        noisy_markers = ("기관목록", "연락처목록", "사후관리기관목록", "결정기관연락처목록")
        return any(marker in normalized for marker in noisy_markers)

    @staticmethod
    def _is_low_value_caution(text: str) -> bool:
        normalized = " ".join(str(text or "").split())
        low_value_patterns = (
            r"선정\s*기준은?\s*지원\s*대상.*참고",
            r"공식\s*안내\s*확인\s*필요",
            r"다시\s*확인해\s*주세요",
        )
        return any(re.search(pattern, normalized, re.IGNORECASE) for pattern in low_value_patterns)

    @classmethod
    def _summary_sentence(cls, text: str | None, limit: int) -> str | None:
        cleaned = cls._clean(text)
        if not cleaned:
            return None
        first = cls._sentences(cleaned)[0] if cls._sentences(cleaned) else cleaned
        return cls._ensure_sentence(cls._shorten(first, limit))

    @classmethod
    def _sentence_with_keywords(
        cls,
        text: str,
        keywords: tuple[str, ...],
    ) -> str | None:
        for sentence in cls._sentences(text):
            if any(keyword in sentence for keyword in keywords):
                return sentence
        return None

    @classmethod
    def _sentences(cls, text: str) -> list[str]:
        cleaned = cls._clean(text)
        if not cleaned:
            return []
        parts = _SENTENCE_RE.split(cleaned)
        if len(parts) == 1:
            parts = re.split(r"\s*(?:\n|[;；])\s*", cleaned)
        return [part.strip(" -:;,.") for part in parts if part.strip(" -:;,.")]

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = str(value).replace("\r", "\n")
        lines = []
        for line in text.splitlines():
            line = _BULLET_RE.sub("", line).strip()
            if line:
                lines.append(line)
        text = " ".join(lines)
        text = re.sub(r"\([^)]{1,12}\)\s*", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip(" -:;,.") or None

    @staticmethod
    def _ensure_sentence(text: str | None) -> str | None:
        if not text:
            return None
        if text[-1] in ".!?。！？":
            return text
        return f"{text}."

    @staticmethod
    def _shorten(text: str | None, limit: int) -> str | None:
        if not text:
            return None
        if len(text) <= limit:
            return text
        window = text[:limit].rstrip()
        for marker in ("다.", "요.", ".", "!", "?"):
            index = window.rfind(marker)
            if index >= max(40, limit // 2):
                return window[: index + len(marker)].strip()
        space_index = window.rfind(" ")
        if space_index >= max(40, limit // 2):
            window = window[:space_index]
        return window.rstrip(" -:;,.") + "..."


def application_status_display(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return APPLICATION_STATUS_DISPLAY.get(text, text)


def quality_flag_display(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return QUALITY_FLAG_DISPLAY.get(text, text)


def life_stage_display(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return LIFE_STAGE_DISPLAY.get(text, text)


def user_status_display(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return USER_STATUS_DISPLAY.get(text, text)


def assessment_status_display(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    return ASSESSMENT_STATUS_DISPLAY.get(text, text)

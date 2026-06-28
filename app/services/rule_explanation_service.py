import re
from typing import Any

# 판정 종류.
VERDICT_MATCHED = "matched"
VERDICT_EXCLUDED = "excluded"
VERDICT_UNCERTAIN = "uncertain"

# 판정별 한국어 접미사.
_VERDICT_SUFFIX = {
    VERDICT_MATCHED: "충족",
    VERDICT_EXCLUDED: "미충족",
    VERDICT_UNCERTAIN: "확인 필요",
}

# field_name → 사용자 노출용 한국어 라벨(원시 enum 노출 방지 폴백).
_FIELD_LABELS = {
    "income_status": "수급 자격",
    "income": "소득 수준",
    "income_level": "소득 수준",
    "median_income_percent": "중위소득",
    "child_age": "자녀 나이",
    "childAge": "자녀 나이",
    "age": "나이",
    "household_member_age": "가구원 나이",
    "stage": "생애주기",
    "special": "특수 상황",
    "special_condition": "특수 상황",
    "region": "지역",
    "household_type": "가구 유형",
    "pregnancy_status": "임신 여부",
    "employment_status": "근로 상태",
    "insurance_status": "보험 자격",
    "worker_status": "근로자 여부",
    "accident_victim_status": "사고 피해 여부",
    "debt_status": "채무 상태",
    "legal_consultation_need": "법률 상담 필요",
}

_MAX_LEN = 40
_MAX_GROUP_ITEM_LEN = 24


class RuleExplanationService:
    """policy_rule 판정 결과를 사용자 노출용 짧은 한국어 사유 문장으로 변환한다.

    우선순위: manual_check_reason(uncertain) → source_text → note(한글 문구만)
    → 기존 reason → field 라벨.
    LLM을 쓰지 않고 결정론적 템플릿/라벨 매핑으로만 생성한다.
    """

    def explain(self, entry: dict[str, Any], verdict: str) -> str:
        """단일 rule/판정 항목을 사유 문장으로 변환한다.

        entry는 source_text/note/manual_check_reason/reason/field(또는 field_name)를
        가질 수 있는 dict(매처 rule 또는 추천 matched/uncertain/excluded 항목).
        """
        if verdict == VERDICT_UNCERTAIN:
            manual_reason = self._clean(entry.get("manual_check_reason"))
            if manual_reason:
                return self._shorten(manual_reason)

        source = self._clean(entry.get("source_text"))
        if source and self._is_displayable(source):
            return self._with_verdict(self._shorten(source), verdict)

        # note는 사람이 읽는 문구일 때만 사용한다(group_key/field_name 같은 내부 토큰 제외).
        note = self._clean(entry.get("note"))
        if note and self._is_human_text(note):
            return self._with_verdict(self._shorten(note), verdict)

        reason = self._clean(entry.get("reason"))
        if reason and self._is_displayable(reason):
            return self._shorten(reason)

        label = self._field_label(entry.get("field") or entry.get("field_name"))
        return self._with_verdict(label, verdict)

    def explain_group(self, rules: list[dict[str, Any]], verdict: str) -> str:
        """OR 그룹(여러 대안 rule)을 하나의 사유 문장으로 묶는다."""
        phrases: list[str] = []
        for rule in rules:
            text = self._clean(rule.get("source_text"))
            if not text:
                note = self._clean(rule.get("note"))
                if note and self._is_human_text(note):
                    text = note
            if text:
                phrases.append(self._shorten(text, _MAX_GROUP_ITEM_LEN))
        phrases = list(dict.fromkeys(phrases))
        if not phrases:
            label = self._field_label(
                rules[0].get("field") or rules[0].get("field_name")
            ) if rules else "해당 조건"
            return self._with_verdict(label, verdict)

        joined = " 또는 ".join(phrases)
        if verdict == VERDICT_MATCHED:
            return f"{joined} 중 충족"
        if verdict == VERDICT_EXCLUDED:
            return f"{joined} 모두 미충족"
        return f"{joined} 중 하나 확인 필요"

    def explain_each(
        self, entries: list[dict[str, Any]], verdict: str
    ) -> list[str]:
        """판정 항목 리스트를 사유 문장 리스트로 변환(중복 제거)."""
        sentences = [self.explain(entry, verdict) for entry in entries]
        return [s for s in dict.fromkeys(sentences) if s]

    def field_label(self, field: Any) -> str:
        """field_name을 사용자 노출용 한국어 라벨로 변환(폴백 표기용)."""
        return self._field_label(field)

    # --- 헬퍼 ---

    def _with_verdict(self, phrase: str, verdict: str) -> str:
        suffix = _VERDICT_SUFFIX.get(verdict)
        return f"{phrase} {suffix}" if suffix else phrase

    def _field_label(self, field: Any) -> str:
        # 라벨 맵에 없는 field는 원시 enum/snake_case 노출 대신 일반 표기로 폴백.
        if not field:
            return "해당 조건"
        return _FIELD_LABELS.get(str(field), "해당 조건")

    def _is_human_text(self, text: str) -> bool:
        """한글이 포함된 사람이 읽는 문구인지(내부 영문 토큰 노출 방지)."""
        return any("가" <= ch <= "힣" for ch in text)

    def _is_displayable(self, text: str) -> bool:
        """사용자 노출 가능한 문구인지. 내부 토큰(대문자/언더스코어 코드)은 제외한다.

        예: "FOSTERCAREPARTICIPATIONNOTMODELED",
        "REFUGEE_APPLICATION_PENDING_EXCLUDED" 같은 규칙 코드가 source_text/reason에
        섞여 들어와도 사용자 카드에 그대로 노출되지 않도록 막는다.
        """
        if self._is_human_text(text):
            return True
        # 한글이 없고 영문 대문자/숫자/언더스코어/구분기호로만 이뤄지면 내부 토큰으로 본다.
        return not bool(re.fullmatch(r"[A-Z0-9][A-Z0-9_./\s-]*", text.strip()))

    def _clean(self, value: Any) -> str | None:
        if value in (None, "", []):
            return None
        text = " ".join(str(value).split())
        return text or None

    def _shorten(self, text: str, max_len: int = _MAX_LEN) -> str:
        text = " ".join(text.split())
        if len(text) <= max_len:
            return text
        return text[:max_len].rstrip() + "…"


def get_rule_explanation_service() -> RuleExplanationService:
    return RuleExplanationService()

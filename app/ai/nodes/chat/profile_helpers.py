from __future__ import annotations

from typing import Any

from app.ai.nodes.chat.slots import (
    CHILD_AGE_TO_STAGE as _CHILD_AGE_TO_STAGE,
    CONFIRM_NO_HINTS as _CONFIRM_NO_HINTS,
    CONFIRM_YES_HINTS as _CONFIRM_YES_HINTS,
    INCOME_BRACKET_TO_PROFILE_CODE as _INCOME_BRACKET_TO_PROFILE_CODE,
    PROFILE_LABELS as _PROFILE_LABELS,
    PROFILE_OPTION_LABELS as _PROFILE_OPTION_LABELS,
    REQUIRED_SLOTS,
    SLOT_LABELS as _SLOT_LABELS,
    SLOT_QUESTIONS as _SLOT_QUESTIONS,
)
from app.ai.states.chat_state import Intent, ProfileSlot


def _filled_slots(profile: ProfileSlot | None) -> set[str]:
    if not profile:
        return set()
    filled: set[str] = set()
    for key in ("stage", "child_age", "income", "region"):
        if profile.get(key):
            filled.add(key)
    if profile.get("special"):
        filled.add("special")
    return filled


def _missing_required(intent: Intent, profile: ProfileSlot | None) -> list[str]:
    required = REQUIRED_SLOTS.get(intent, ())
    filled = _filled_slots(profile)
    # 사용자가 한 번 "건너뛰기"한 슬롯은 다시 묻지 않는다.
    skipped = set((profile or {}).get("skipped") or [])
    return [slot for slot in required if slot not in filled and slot not in skipped]


def _merge_profile(
    base: ProfileSlot | None, extracted: dict[str, Any] | None
) -> ProfileSlot:
    merged: dict[str, Any] = dict(base or {})
    if not extracted:
        return merged  # type: ignore[return-value]
    for key in ("stage", "child_age", "income", "region"):
        value = extracted.get(key)
        if value:
            merged[key] = value
    special = extracted.get("special")
    if special:
        existing = list(merged.get("special") or [])
        for item in special:
            if item and item not in existing:
                existing.append(item)
        merged["special"] = existing
    return merged  # type: ignore[return-value]


def _format_profile_context(profile: ProfileSlot | None) -> str:
    if not profile:
        return "(없음)"
    parts: list[str] = []
    for key, label in _SLOT_LABELS.items():
        if key == "special":
            continue
        if profile.get(key):
            parts.append(f"- {label}: {_profile_value_label(key, profile[key])}")
    if profile.get("special"):
        parts.append(f"- 특이사항: {_profile_value_label('special', profile['special'])}")
    return "\n".join(parts) if parts else "(없음)"


def _build_slot_question(awaiting: list[str], profile: ProfileSlot | None) -> str:
    if not awaiting:
        return "조금 더 알려주시면 맞춤으로 찾아드릴게요."
    first = awaiting[0]
    question = _SLOT_QUESTIONS.get(first, f"{first}을(를) 알려주세요.")
    if len(awaiting) == 1:
        return f"맞춤 추천을 위해 하나만 여쭤볼게요. {question}"
    rest = ", ".join(_SLOT_LABELS.get(slot, slot) for slot in awaiting[1:])
    return f"맞춤 추천을 위해 몇 가지만 여쭤볼게요. 먼저 {question} (이어서 {rest}도 확인할게요.)"


def _profile_to_selected_conditions(profile: ProfileSlot | None) -> dict[str, Any]:
    """수집한 프로필(코드값)을 추천 엔진의 selected_conditions 형태로 변환."""
    if not profile:
        return {}
    out: dict[str, Any] = {}
    stage = profile.get("stage")
    child_age = profile.get("child_age")
    if child_age:
        out["childAge"] = child_age
        # child_age 는 직접 필터가 아니므로 stage 로 파생(명시 stage 가 없을 때).
        if not stage:
            stage = _CHILD_AGE_TO_STAGE.get(child_age)
    if stage:
        out["stage"] = stage
    if profile.get("income"):
        out["income"] = profile["income"]
    if profile.get("region"):
        out["region"] = profile["region"]
    special = profile.get("special")
    if special:
        out["special"] = [item for item in special if item]
    return out


def _profile_value_label(key: str, value: Any) -> str:
    if isinstance(value, list):
        if not value:
            return "해당 없음"
        return ", ".join(_profile_value_label(key, item) for item in value)
    text = str(value)
    if key == "income" and text in _INCOME_BRACKET_TO_PROFILE_CODE:
        text = _INCOME_BRACKET_TO_PROFILE_CODE[text]
    return _PROFILE_OPTION_LABELS.get(key, {}).get(text, text)


def _profile_summary_from_snapshot(
    snap: dict[str, Any],
    *,
    income_bracket: str | None = None,
    region_code: str | None = None,
    household_type: str | None = None,
    pregnancy_status: bool = False,
) -> list[str] | None:
    stage = snap.get("stage") or snap.get("life_stage")
    child_age = snap.get("childAge") or snap.get("child_age")
    income = snap.get("income") or snap.get("income_level")
    region = snap.get("region") or snap.get("region_code")
    special = snap.get("special")

    if not stage and pregnancy_status:
        stage = "pregnant"
    if not income and income_bracket:
        income = income_bracket
    if not region and region_code:
        region = region_code
    if special is None and household_type:
        special = [household_type]

    parts: list[str] = []
    rows = (
        ("stage", stage),
        ("child_age", child_age),
        ("income", income),
        ("region", region),
        ("special", special),
    )
    for key, value in rows:
        if value is None or value == "":
            continue
        parts.append(f"{_PROFILE_LABELS[key]}: {_profile_value_label(key, value)}")

    return parts or None


def _interpret_confirm(text: str | None) -> str | None:
    """확인 프롬프트에 대한 사용자 응답을 yes/no/None 으로 해석."""
    normalized = (text or "").strip().lower()
    if not normalized:
        return None
    if any(keyword in normalized for keyword in _CONFIRM_NO_HINTS):
        return "no"
    if any(keyword in normalized for keyword in _CONFIRM_YES_HINTS):
        return "yes"
    return None

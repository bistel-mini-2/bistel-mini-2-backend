import asyncio
from typing import Any

from app.services.chat_handlers import handle_confirm_profile, build_assistant_payload


def _state(*, summary: list[str] | None = None) -> dict[str, Any]:
    return {
        "user_id": 1,
        "user_content": "네",
        "history": [],
        "supervisor_decision": {"intent": "recommend", "raw": "{}"},
        "profile_confirm": {
            "summary": summary or ["생애단계: 영유아", "소득: 하위 50%"],
            "options": [
                {"label": "네, 이 정보로 추천해줘", "value": "yes"},
                {"label": "아니요, 다시 입력할게요", "value": "no"},
            ],
        },
        "profile": {},
    }


def test_confirm_profile_formats_summary_in_content() -> None:
    summary = ["생애단계: 영유아", "소득: 하위 50%"]
    result = asyncio.run(handle_confirm_profile(_state(summary=summary)))

    assert "생애단계: 영유아" in result["branch_content"]
    assert "소득: 하위 50%" in result["branch_content"]
    assert result["branch_policies"] == []
    assert result["branch_evidences"] == []


def test_confirm_profile_sets_pending_confirm_kind() -> None:
    result = asyncio.run(handle_confirm_profile(_state()))

    pending = result["pending"]
    assert pending["kind"] == "confirm"
    assert pending["intent"] == "recommend"
    assert pending["awaiting"] == []


def test_confirm_profile_preserves_profile_confirm_in_state() -> None:
    state = _state(summary=["지역: 서울"])
    result = asyncio.run(handle_confirm_profile(state))

    assert result["profile_confirm"] == state["profile_confirm"]


def test_confirm_profile_payload_includes_profile_confirm(monkeypatch) -> None:
    result = asyncio.run(handle_confirm_profile(_state()))
    payload_state = asyncio.run(build_assistant_payload(result))

    payload = payload_state["assistant_payload"]
    assert payload["profile_confirm"] is not None
    assert payload["disclaimer"] is False

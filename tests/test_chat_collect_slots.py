import asyncio
from typing import Any

from app.ai.nodes.chat.slots import RECOMMEND_WIZARD_FIELDS
from app.services.chat_handlers import handle_collect_slots


def _state(
    *,
    intent: str = "recommend",
    awaiting: list[str] | None = None,
    profile: dict[str, Any] | None = None,
    resolved_slug: str | None = None,
) -> dict[str, Any]:
    decision: dict[str, Any] = {"intent": intent, "raw": "{}"}
    if resolved_slug:
        decision["resolved_policy_slug"] = resolved_slug
    return {
        "user_id": 1,
        "user_content": "조건에 맞는 정책 찾아줘",
        "history": [],
        "supervisor_decision": decision,
        "awaiting_slots": awaiting or ["child_age"],
        "profile": profile or {},
    }


def test_collect_slots_recommend_uses_wizard_order() -> None:
    result = asyncio.run(handle_collect_slots(_state(intent="recommend")))

    field_keys = [f["key"] for f in result["slot_request"]["fields"]]
    wizard_order = list(RECOMMEND_WIZARD_FIELDS)
    assert field_keys == wizard_order


def test_collect_slots_recommend_asks_conditions_not_policy_name() -> None:
    result = asyncio.run(
        handle_collect_slots(
            {
                **_state(intent="recommend"),
                "user_content": "추천해줘",
            }
        )
    )

    field_keys = [f["key"] for f in result["slot_request"]["fields"]]
    assert "child_age" in field_keys
    assert "summary_target" not in field_keys
    assert "정책명" not in result["branch_content"]


def test_collect_slots_recommend_skips_filled_slots() -> None:
    profile = {"child_age": "0", "income": "low"}
    result = asyncio.run(
        handle_collect_slots(_state(intent="recommend", profile=profile))
    )

    field_keys = [f["key"] for f in result["slot_request"]["fields"]]
    assert "child_age" not in field_keys
    assert "income" not in field_keys
    assert "region" in field_keys


def test_collect_slots_other_intent_uses_awaiting_order() -> None:
    awaiting = ["region", "income"]
    result = asyncio.run(
        handle_collect_slots(
            _state(intent="eligibility", awaiting=awaiting)
        )
    )

    field_keys = [f["key"] for f in result["slot_request"]["fields"]]
    assert field_keys == awaiting


def test_collect_slots_pending_set_correctly() -> None:
    result = asyncio.run(handle_collect_slots(_state(intent="recommend")))

    pending = result["pending"]
    assert pending["intent"] == "recommend"
    assert pending["kind"] == "slot"
    assert isinstance(pending["awaiting"], list)
    assert pending["asked"] == [pending["awaiting"][0]]


def test_collect_slots_slot_request_flow_type_matches_intent() -> None:
    result = asyncio.run(
        handle_collect_slots(_state(intent="eligibility", awaiting=["region"]))
    )

    assert result["slot_request"]["flow_type"] == "eligibility"
    assert result["branch_policies"] == []
    assert result["branch_evidences"] == []

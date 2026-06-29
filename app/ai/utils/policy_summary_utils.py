from typing import Any

from app.ai.utils.condition_profile_utils import (
    condition_profile_notes,
    summarize_condition_tree,
)


def build_policy_summary_key_points(
    policy: dict[str, Any],
    *,
    content_limit: int,
) -> list[dict[str, str]]:
    candidates = _policy_summary_key_point_candidates(policy)
    key_points: list[dict[str, str]] = []
    for label, value in candidates:
        text = " ".join(str(value or "").split())
        if text:
            key_points.append(
                {
                    "label": label,
                    "content": _short(text, content_limit),
                }
            )
    return key_points[:3]


def _policy_summary_key_point_candidates(
    policy: dict[str, Any],
) -> tuple[tuple[str, Any], ...]:
    condition_json = _condition_json(policy)
    condition_text = (
        policy.get("condition_profile_target_summary")
        or summarize_condition_tree(condition_json.get("condition_tree"))
        or policy.get("condition_profile_source_text")
    )
    condition_notes = condition_profile_notes(condition_json)
    return (
        ("target", condition_text or policy.get("target_description")),
        (
            "benefit",
            policy.get("benefit_description") or policy.get("benefit_summary"),
        ),
        (
            "application",
            policy.get("application_method")
            or policy.get("application_period_text"),
        ),
        ("condition_check", condition_notes),
    )


def _condition_json(policy: dict[str, Any]) -> dict[str, Any]:
    value = policy.get("condition_profile_json")
    return value if isinstance(value, dict) else {}


def _short(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."

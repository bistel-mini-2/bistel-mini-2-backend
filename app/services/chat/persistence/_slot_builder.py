from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SLOT_MAX_POLICIES = 4


def _similar_turn_base_entry(
    existing: list[dict],
    branch_policies: list[dict],
    base_slug: str | None,
) -> dict | None:
    if base_slug:
        for entry in existing:
            if entry.get("slug") == base_slug:
                return entry

    base_src = None
    if base_slug:
        base_src = next(
            (p for p in branch_policies if p.get("slug") == base_slug), None
        )
    if base_src is None and branch_policies:
        base_src = branch_policies[0]
    if not base_src or not base_src.get("slug"):
        return None
    try:
        base_pid = int(base_src["policy_id"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "policy_id": base_pid,
        "slug": base_src["slug"],
        "policy_name": base_src.get("policy_name") or base_src.get("name") or "",
        "last_action": "VIEWED",
    }


def build_next_slot(
    *,
    current_slot: dict,
    policy_links: list[dict],
    branch_policies: list[dict],
    slug_to_policy_id: dict[str, int],
    profile: dict | None = None,
    pending: dict | None = None,
    eligibility_slot_update: dict | None = None,
    similar_policies: list[dict] | None = None,
    base_slug: str | None = None,
    last_intent: str | None = None,
    last_result_type: str | None = None,
    suggested_actions: list[str] | None = None,
) -> dict | None:
    slug_to_action: dict[str, str] = {}
    for link in policy_links:
        slug = link.get("policy_slug")
        action = link.get("action_type")
        if slug and action and slug not in slug_to_action:
            slug_to_action[slug] = action

    slug_to_name: dict[str, str] = {}
    for policy in branch_policies:
        slug = policy.get("slug")
        if slug and slug not in slug_to_name:
            slug_to_name[slug] = policy.get("policy_name") or ""

    new_entries: list[dict] = []
    seen: set[str] = set()
    for policy in branch_policies:
        slug = policy.get("slug")
        if not slug or slug in seen:
            continue
        policy_id = slug_to_policy_id.get(slug)
        action = slug_to_action.get(slug)
        if policy_id is None or action is None:
            continue
        seen.add(slug)
        new_entries.append({
            "policy_id": policy_id,
            "slug": slug,
            "policy_name": slug_to_name.get(slug) or "",
            "last_action": action,
        })

    for sim in similar_policies or []:
        slug = sim.get("slug")
        if not slug or slug in seen:
            continue
        try:
            sim_policy_id = int(sim["policy_id"])
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(slug)
        new_entries.append({
            "policy_id": sim_policy_id,
            "slug": slug,
            "policy_name": sim.get("name") or sim.get("policy_name") or "",
            "last_action": "SIMILAR_POLICY",
        })

    has_context_updates = (
        last_intent is not None
        or last_result_type is not None
        or suggested_actions is not None
    )
    if (
        not new_entries
        and profile is None
        and pending is None
        and not eligibility_slot_update
        and not has_context_updates
    ):
        return None

    if new_entries:
        existing = list(current_slot.get("recent_policies") or [])
        merged: list[dict] = []
        if similar_policies:
            base_entry = _similar_turn_base_entry(existing, branch_policies, base_slug)
            if base_entry and base_entry.get("slug") not in seen:
                merged.append(base_entry)
                seen.add(base_entry["slug"])
        merged.extend(new_entries)
        for entry in existing:
            slug = entry.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            merged.append(entry)
            if len(merged) >= _SLOT_MAX_POLICIES:
                break
        recent_policies = merged[:_SLOT_MAX_POLICIES]
    else:
        recent_policies = list(current_slot.get("recent_policies") or [])

    if eligibility_slot_update:
        update_slug = eligibility_slot_update.get("slug")
        if not any(p.get("slug") == update_slug for p in recent_policies):
            logger.warning(
                "eligibility_slot_update slug %r not found in recent_policies; update skipped",
                update_slug,
            )
        recent_policies = [
            {
                **p,
                "eligibility_request_id": eligibility_slot_update.get("eligibility_request_id"),
                "follow_up_questions": eligibility_slot_update.get("follow_up_questions"),
                "eligibility_status": eligibility_slot_update.get("eligibility_status"),
            }
            if p.get("slug") == update_slug
            else p
            for p in recent_policies
        ]

    next_profile = (
        profile if profile is not None else current_slot.get("profile") or {}
    )

    next_slot: dict = {
        "recent_policies": recent_policies,
        "profile": next_profile,
        "pending": pending,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # 대화 맥락 필드: 명시적으로 전달된 경우에만 갱신, 없으면 기존 값 유지
    next_slot["last_intent"] = (
        last_intent if last_intent is not None else current_slot.get("last_intent")
    )
    next_slot["last_result_type"] = (
        last_result_type if last_result_type is not None else current_slot.get("last_result_type")
    )
    next_slot["suggested_actions"] = (
        suggested_actions if suggested_actions is not None else current_slot.get("suggested_actions") or []
    )
    return next_slot

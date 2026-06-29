from typing import Any


def summarize_condition_tree(value: Any) -> str:
    leaves = condition_leaves(value)
    parts: list[str] = []
    for leaf in leaves[:4]:
        source_text = leaf.get("source_text")
        if source_text:
            parts.append(str(source_text))
            continue
        field = leaf.get("field")
        operator = leaf.get("operator")
        leaf_value = leaf.get("value")
        if field and operator:
            parts.append(f"{field} {operator} {leaf_value}")
    return " / ".join(part for part in parts if part)


def condition_leaves(value: Any) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    if not isinstance(value, dict):
        return leaves
    children = value.get("conditions")
    if isinstance(children, list):
        for child in children:
            leaves.extend(condition_leaves(child))
    elif value.get("field"):
        leaves.append(value)
    return leaves


def summarize_condition_profile_notes(
    *,
    exclusions: Any,
    unsupported: Any,
    unknowns: Any,
) -> str:
    notes: list[str] = []
    for label, items in (
        ("exclusions", exclusions),
        ("manual review", unsupported),
        ("unknown", unknowns),
    ):
        if isinstance(items, list) and items:
            text = first_condition_item_text(items)
            notes.append(f"{label}: {text}" if text else label)
    return " / ".join(notes)


def condition_profile_notes(condition_json: dict[str, Any]) -> str:
    return summarize_condition_profile_notes(
        exclusions=condition_json.get("exclusions"),
        unsupported=condition_json.get("unsupported_conditions"),
        unknowns=condition_json.get("unknowns"),
    )


def first_condition_item_text(items: list[Any]) -> str:
    for item in items:
        if isinstance(item, dict):
            value = item.get("source_text") or item.get("reason")
            if value:
                return str(value)
    return ""

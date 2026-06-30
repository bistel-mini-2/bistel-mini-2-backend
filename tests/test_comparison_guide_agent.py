import asyncio
import json
from decimal import Decimal

from app.ai.agents.comparison_guide_agent import ComparisonGuideAgent


def test_comparison_guide_agent_returns_fallback_without_openai_key(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.ai.agents.comparison_guide_agent.settings.openai_api_key",
        None,
    )
    fallback = "A 정책은 비용 부담을 줄이는 데 장점이 있고, B 정책은 돌봄 공백을 메우는 데 장점이 있습니다."

    result = asyncio.run(
        ComparisonGuideAgent().rewrite_selection_guide(
            policy_a={"name": "A 정책"},
            policy_b={"name": "B 정책"},
            diff_table=[],
            fallback_guide=fallback,
        )
    )

    assert result == fallback


def test_comparison_guide_agent_json_safe_converts_decimal() -> None:
    payload = ComparisonGuideAgent()._json_safe(
        {
            "confidence": Decimal("0.9200"),
            "nested": [{"score": Decimal("1.5")}],
        }
    )

    assert payload == {"confidence": 0.92, "nested": [{"score": 1.5}]}
    json.dumps(payload, ensure_ascii=False)


def test_comparison_guide_agent_policy_payload_excludes_condition_json() -> None:
    payload = ComparisonGuideAgent()._policy_payload(
        {
            "name": "A 정책",
            "condition_profile_target_summary": "대상 요약",
            "condition_profile_json": {"condition_tree": {"operator": "AND"}},
        }
    )

    assert payload["name"] == "A 정책"
    assert payload["target_summary"] == "대상 요약"
    assert "condition_json" not in payload

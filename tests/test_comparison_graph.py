import asyncio
from unittest.mock import AsyncMock

from app.ai.graphs.comparison_graph import ComparisonGraphRunner
from app.schemas.compare_schema import (
    CompareDiffItem,
    ComparePolicySummary,
    PolicyCompareResponse,
)


def test_comparison_graph_calls_compare_service() -> None:
    compare_service = AsyncMock()
    compare_service.compare_policies = AsyncMock(
        return_value=PolicyCompareResponse(
            policy_a=ComparePolicySummary(
                policy_id="1",
                slug="WLF1",
                name="A 정책",
                summary={"condition": "A 조건", "benefit": "현금"},
            ),
            policy_b=ComparePolicySummary(
                policy_id="2",
                slug="WLF2",
                name="B 정책",
                summary={"condition": "B 조건", "benefit": "바우처"},
            ),
            diff_table=[
                CompareDiffItem(field="지원 대상 요약", a="A 조건", b="B 조건"),
            ],
            selection_guide="두 정책은 지원 대상 조건이 다릅니다.",
            related_policies=[],
        )
    )
    fake_db = object()

    result = asyncio.run(
        ComparisonGraphRunner(compare_service=compare_service).run(
            fake_db,  # type: ignore[arg-type]
            slug_a="WLF1",
            slug_b="WLF2",
            user_id=7,
            raw_query="두 정책 비교",
        )
    )

    compare_service.compare_policies.assert_awaited_once_with(
        fake_db,
        slug_a="WLF1",
        slug_b="WLF2",
        user_id=7,
    )
    assert result["policy_a"]["slug"] == "WLF1"
    assert result["policy_b"]["slug"] == "WLF2"
    assert result["diff_table"][0]["field"] == "지원 대상 요약"

import asyncio
from types import SimpleNamespace

from app.ai.graphs.eligibility_graph import EligibilityGraphRunner


class _FakeEligibilityResponse:
    def model_dump(self, mode: str = "json") -> dict:
        return {
            "request_id": "77",
            "status": "COMPLETED",
            "policy_id": "12",
            "slug": "WLF1",
            "policy_name": "아이돌봄서비스",
            "user_status": "NEEDS_CONFIRMATION",
        }


class _FakeLifecycleService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def create_eligibility_request(self, **kwargs):
        self.calls.append(("create", kwargs))
        return SimpleNamespace(request_id="77")

    async def mark_processing(self, **kwargs):
        self.calls.append(("mark_processing", kwargs))

    async def process_condition_request(self, **kwargs):
        self.calls.append(("process", kwargs))

    async def get_eligibility_result(self, **kwargs):
        self.calls.append(("get_result", kwargs))
        return _FakeEligibilityResponse()


def test_eligibility_graph_runs_common_lifecycle_in_order() -> None:
    lifecycle = _FakeLifecycleService()
    runner = EligibilityGraphRunner(lifecycle_service=lifecycle)

    result = asyncio.run(
        runner.run(
            db=object(),
            user_id=3,
            policy_identifier="WLF1",
            raw_query="나도 받을 수 있어?",
            source_type="CHAT",
        )
    )

    assert [name for name, _ in lifecycle.calls] == [
        "create",
        "mark_processing",
        "process",
        "get_result",
    ]
    assert lifecycle.calls[0][1]["policy_identifier"] == "WLF1"
    assert lifecycle.calls[0][1]["source_type"] == "CHAT"
    assert lifecycle.calls[2][1]["request_type"] == "eligibility"
    assert result["slug"] == "WLF1"
    assert result["user_status"] == "NEEDS_CONFIRMATION"

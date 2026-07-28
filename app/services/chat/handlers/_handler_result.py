from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.states.chat_state import PendingState


@dataclass
class HandlerResult(Mapping[str, Any]):
    """핸들러 반환 타입. branch_* dict 패턴을 대체한다.

    마이그레이션 중에는 to_state_patch()로 기존 state.update() 방식과 호환 가능.
    """

    content: str
    policies: list[dict[str, Any]] = field(default_factory=list)
    evidences: list[dict[str, Any]] = field(default_factory=list)
    apply_card: dict[str, Any] | None = None
    easy_summary: str | None = None
    key_points: list[dict[str, Any]] = field(default_factory=list)
    eligibility_result: dict[str, Any] | None = None
    user_status: str | None = None
    suggested_actions: list[str] = field(default_factory=list)
    policy_candidates: list[dict[str, Any]] = field(default_factory=list)
    slot_request: dict[str, Any] | None = None
    profile_confirm: dict[str, Any] | None = None
    pending: PendingState | None = None
    eligibility_slot_update: dict[str, Any] | None = None

    @classmethod
    def from_state_patch(cls, value: Mapping[str, Any]) -> "HandlerResult":
        """이전 branch_* dict 반환값을 새 핸들러 계약으로 변환한다."""
        return cls(
            content=str(value.get("branch_content") or value.get("content") or ""),
            policies=list(value.get("branch_policies") or value.get("policies") or []),
            evidences=list(value.get("branch_evidences") or value.get("evidences") or []),
            apply_card=value.get("branch_apply_card") or value.get("apply_card"),
            easy_summary=value.get("branch_easy_summary") or value.get("easy_summary"),
            key_points=list(value.get("branch_key_points") or value.get("key_points") or []),
            eligibility_result=(
                value.get("branch_eligibility_result")
                or value.get("eligibility_result")
            ),
            user_status=value.get("branch_user_status") or value.get("user_status"),
            suggested_actions=list(
                value.get("branch_suggested_actions")
                or value.get("suggested_actions")
                or []
            ),
            policy_candidates=list(
                value.get("branch_policy_candidates")
                or value.get("policy_candidates")
                or []
            ),
            slot_request=value.get("slot_request"),
            profile_confirm=value.get("profile_confirm"),
            pending=value.get("pending"),
            eligibility_slot_update=value.get("eligibility_slot_update"),
        )

    def to_state_patch(self) -> dict[str, Any]:
        """기존 state.update() 패턴과 호환되는 dict를 반환한다.

        핸들러를 HandlerResult로 전환하는 동안 _routing.py가 이 메서드를 호출해
        기존 코드를 건드리지 않고 점진적으로 마이그레이션할 수 있다.
        """
        patch = {
            "branch_content": self.content,
            "branch_policies": self.policies,
            "branch_evidences": self.evidences,
            "branch_apply_card": self.apply_card,
            "branch_easy_summary": self.easy_summary,
            "branch_key_points": self.key_points,
            "branch_eligibility_result": self.eligibility_result,
            "branch_user_status": self.user_status,
            "branch_suggested_actions": self.suggested_actions,
            "branch_policy_candidates": self.policy_candidates,
            "slot_request": self.slot_request,
            "profile_confirm": self.profile_confirm,
        }
        if self.pending is not None:
            patch["pending"] = self.pending
        if self.eligibility_slot_update is not None:
            patch["eligibility_slot_update"] = self.eligibility_slot_update
        return patch

    def __getitem__(self, key: str) -> Any:
        try:
            return self.to_state_patch()[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_state_patch())

    def __len__(self) -> int:
        return len(self.to_state_patch())

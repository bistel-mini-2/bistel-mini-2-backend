from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

ProgressCallback = Callable[[str, str, str, int, int], Awaitable[None]]

_PROGRESS_CALLBACK: ContextVar[ProgressCallback | None] = ContextVar(
    "graph_node_progress_callback", default=None
)


def set_progress_callback(cb: ProgressCallback | None):
    return _PROGRESS_CALLBACK.set(cb)


def reset_progress_callback(token) -> None:
    _PROGRESS_CALLBACK.reset(token)


async def emit_progress(flow: str, node: str, status: str, step: int, total: int) -> None:
    cb = _PROGRESS_CALLBACK.get()
    if cb is not None:
        await cb(flow, node, status, step, total)


NODE_REGISTRY: dict[str, list[dict[str, str]]] = {
    "recommendation": [
        {"node": "candidate_search",  "label": "후보 정책 검색 중"},
        {"node": "rule_filter",       "label": "규칙 필터 적용 중"},
        {"node": "candidate_save",    "label": "후보 저장 중"},
        {"node": "policy_assessment", "label": "정책 적합성 판정 중"},
        {"node": "assessment_save",   "label": "판정 결과 저장 중"},
        {"node": "build_result",      "label": "결과 구성 중"},
        {"node": "llm_rerank",        "label": "AI 재순위 산정 중"},
        {"node": "rerank_save",       "label": "재순위 결과 저장 중"},
        {"node": "finalize_result",   "label": "최종 결과 확정 중"},
    ],
    "eligibility": [
        {"node": "create_request",  "label": "요청 생성 중"},
        {"node": "mark_processing", "label": "처리 시작 중"},
        {"node": "assess_policy",   "label": "지원 가능성 판정 중"},
        {"node": "build_result",    "label": "결과 구성 중"},
    ],
    "comparison": [
        {"node": "compare_policies", "label": "정책 비교 분석 중"},
        {"node": "build_result",     "label": "비교 결과 구성 중"},
    ],
    "policy_summary": [
        {"node": "summary_evidence_search", "label": "관련 근거 검색 중"},
        {"node": "policy_summary",          "label": "정책 요약 생성 중"},
    ],
}


def get_node_meta(flow: str, node: str) -> tuple[int, int]:
    nodes = NODE_REGISTRY.get(flow, [])
    total = len(nodes)
    for i, entry in enumerate(nodes):
        if entry["node"] == node:
            return i + 1, total
    return 0, total


def get_node_label(flow: str, node: str, status: str) -> str:
    for entry in NODE_REGISTRY.get(flow, []):
        if entry["node"] == node:
            label = entry["label"]
            if status == "completed":
                return label.removesuffix(" 중") + " 완료"
            return label
    return node


def progress_node(flow: str, node: str):
    """노드 함수 데코레이터 — 진입/반환 시 progress 이벤트를 emit한다."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            step, total = get_node_meta(flow, node)
            await emit_progress(flow, node, "started", step, total)
            try:
                result = await func(*args, **kwargs)
                await emit_progress(flow, node, "completed", step, total)
                return result
            except Exception:
                await emit_progress(flow, node, "failed", step, total)
                raise
        return wrapper
    return decorator

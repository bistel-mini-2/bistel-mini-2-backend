from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.ai.graphs.comparison_graph import ComparisonGraphRunner
    from app.ai.graphs.eligibility_graph import EligibilityGraphRunner
    from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService
    from app.services.policy_rag_service import PolicyRagService

_RAG_SERVICE: PolicyRagService | None = None
_LIFECYCLE_SERVICE: AiRequestLifecycleService | None = None
_ELIGIBILITY_GRAPH: EligibilityGraphRunner | None = None
_COMPARISON_GRAPH: ComparisonGraphRunner | None = None
_POLICY_SUMMARY_GRAPH: PolicySummaryGraphRunner | None = None


def get_rag_service() -> PolicyRagService:
    global _RAG_SERVICE
    if _RAG_SERVICE is None:
        from app.services.policy_rag_service import PolicyRagService
        _RAG_SERVICE = PolicyRagService()
    return _RAG_SERVICE


def get_lifecycle_service() -> AiRequestLifecycleService:
    global _LIFECYCLE_SERVICE
    if _LIFECYCLE_SERVICE is None:
        from app.ai.nodes.chat.chat_nodes import _lifecycle_service_class
        _LIFECYCLE_SERVICE = _lifecycle_service_class()()
    return _LIFECYCLE_SERVICE


def get_eligibility_graph() -> EligibilityGraphRunner:
    global _ELIGIBILITY_GRAPH
    if _ELIGIBILITY_GRAPH is None:
        from app.ai.graphs.eligibility_graph import EligibilityGraphRunner
        _ELIGIBILITY_GRAPH = EligibilityGraphRunner()
    return _ELIGIBILITY_GRAPH


def get_comparison_graph() -> ComparisonGraphRunner:
    global _COMPARISON_GRAPH
    if _COMPARISON_GRAPH is None:
        from app.ai.graphs.comparison_graph import ComparisonGraphRunner
        _COMPARISON_GRAPH = ComparisonGraphRunner()
    return _COMPARISON_GRAPH


def get_policy_summary_graph() -> PolicySummaryGraphRunner:
    global _POLICY_SUMMARY_GRAPH
    if _POLICY_SUMMARY_GRAPH is None:
        from app.ai.graphs.policy_summary_graph import PolicySummaryGraphRunner
        _POLICY_SUMMARY_GRAPH = PolicySummaryGraphRunner()
    return _POLICY_SUMMARY_GRAPH

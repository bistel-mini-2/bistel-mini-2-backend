from __future__ import annotations

import logging
from typing import Literal, NotRequired, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.policy_rag_service import PolicyRagService


logger = logging.getLogger(f"{__name__}.ChatSupervisor")


Intent = Literal[
    "recommend",
    "eligibility",
    "compare",
    "apply",
    "policy_summary",
    "unclear",
]


class HistoryMessage(TypedDict):
    role: str
    content: str | None


class SupervisorDecision(TypedDict):
    intent: Intent
    raw: str


class ChatGraphState(TypedDict):
    user_id: int
    user_content: str
    history: list[HistoryMessage]
    supervisor_decision: NotRequired[SupervisorDecision]
    assistant_payload: NotRequired[dict]


class _IntentDecision(BaseModel):
    intent: Intent = Field(description="사용자 메시지의 의도 분류")


SUPERVISOR_SYSTEM = """당신은 임신·출산·육아 정책 챗봇의 Supervisor입니다.
사용자 메시지를 다음 6개 intent 중 하나로 분류하세요.

- recommend: 본인 상황에 맞는 정책 추천 요청 (예: "맞는 정책 알려줘")
- eligibility: 특정 정책의 지원 자격이나 가능성 (예: "나도 받을 수 있어?")
- compare: 정책 2개 비교 (예: "A랑 B 중 뭐가 나아?")
- apply: 신청 방법·필요 서류·기한 (예: "어떻게 신청해?")
- policy_summary: 정책 내용 설명·요약 또는 일반 정책 정보 질의 (예: "이 정책이 뭐야?")
- unclear: 위에 명확히 속하지 않거나 정책과 무관

직전 대화 맥락도 함께 고려해서 결정합니다.
"""


_BRANCH_SYSTEM_PROMPTS: dict[Intent, str] = {
    "policy_summary": """당신은 임신·출산·육아 정책을 안내하는 챗봇입니다.
주어진 정책 문서 발췌(참고 자료)만 근거로 한국어 3~5문장으로 답변하세요.
- 발췌에 없는 내용은 추측하지 마세요.
- 본문에 면책 안내 문장을 넣지 마세요 (별도 필드로 처리).""",
    "recommend": """사용자의 상황에 맞는 정책을 추천하는 챗봇입니다.
참고 자료의 정책 중 사용자 질문과 관련 있어 보이는 정책을 짧게 소개하세요.
- 정확한 추천은 '맞춤 추천' 화면에서 받을 수 있음을 자연스럽게 안내하세요.
- 한국어 3~5문장.""",
    "eligibility": """사용자가 특정 정책의 지원 가능 여부를 묻고 있습니다.
참고 자료의 정책 조건을 근거로 일반적인 답변을 하되, 정확한 판단은 '지원 가능성 분석' 화면을 안내하세요.
- 한국어 3~5문장.""",
    "compare": """사용자가 정책 비교를 묻고 있습니다.
참고 자료의 정책 중 관련된 정책의 차이점을 간단히 설명하고, 자세한 비교는 '정책 비교' 화면을 안내하세요.
- 한국어 3~5문장.""",
    "apply": """사용자가 신청 방법 또는 필요 서류를 묻고 있습니다.
참고 자료의 정책 신청 정보를 근거로 답하고, 단계별 안내는 '신청 준비' 화면을 안내하세요.
- 한국어 3~5문장.""",
    "unclear": """사용자 질문을 정확히 이해하기 어렵습니다.
- 챗봇이 도울 수 있는 주제(정책 추천 / 지원가능성 / 비교 / 신청 / 정책 정보)를 짧게 안내하세요.
- 예시 질문 1~2개를 제안하세요.
- 한국어 2~3문장.
- 정책 자료는 사용하지 마세요.""",
}


_INTENT_TO_ACTION: dict[Intent, str | None] = {
    "recommend": "recommend",
    "eligibility": "eligibility",
    "compare": "compare",
    "apply": "apply",
    "policy_summary": "chat",
    "unclear": None,
}


_LLM_MODEL = "gpt-4o-mini"
HISTORY_LIMIT = 5
_RAG_TOP_K = 5
_POLICIES_MAX = 3
_EVIDENCES_MAX = 5
_SNIPPET_LIMIT = 300


def _llm() -> ChatOpenAI:
    kwargs: dict = {"model": _LLM_MODEL, "temperature": 0.2}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


def _history_to_lc_messages(history: list[HistoryMessage]) -> list[BaseMessage]:
    messages: list[BaseMessage] = []
    for entry in history:
        content = entry.get("content")
        if not content:
            continue
        if entry.get("role") == "user":
            messages.append(HumanMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


async def _classify_intent(state: ChatGraphState) -> dict:
    llm = _llm().with_structured_output(_IntentDecision)
    messages: list[BaseMessage] = [SystemMessage(content=SUPERVISOR_SYSTEM)]
    messages.extend(_history_to_lc_messages(state["history"]))
    messages.append(HumanMessage(content=state["user_content"]))

    try:
        decision = await llm.ainvoke(messages)
        intent: Intent = decision.intent
        raw = decision.model_dump_json()
    except Exception as exc:
        logger.exception("Intent classification failed; falling back to unclear")
        intent = "unclear"
        raw = f"error: {exc}"

    return {"supervisor_decision": {"intent": intent, "raw": raw}}


async def _rag_lookup(query: str) -> tuple[list[dict], list[dict]]:
    rag = PolicyRagService()
    try:
        result = await rag.search(query=query, k=_RAG_TOP_K)
    except Exception:
        logger.exception("RAG lookup failed; returning empty context")
        return [], []

    evidences: list[dict] = []
    seen_chunks: set[int] = set()
    for chunk in result.results:
        if chunk.chunk_id is None or chunk.chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk.chunk_id)
        evidences.append({
            "snippet": (chunk.chunk_text or "")[:_SNIPPET_LIMIT],
            "source_title": chunk.policy_name,
            "source_url": chunk.source_url,
            "evidence_role": None,
        })
        if len(evidences) >= _EVIDENCES_MAX:
            break

    policies: list[dict] = []
    seen_policies: set[str] = set()
    for chunk in result.results:
        if not chunk.policy_code or chunk.policy_code in seen_policies:
            continue
        seen_policies.add(chunk.policy_code)
        policies.append({
            "policy_id": chunk.policy_code,
            "slug": chunk.policy_code,
            "policy_name": chunk.policy_name or "",
            "summary": None,
            "tag": None,
            "tagTone": None,
        })
        if len(policies) >= _POLICIES_MAX:
            break

    return policies, evidences


async def _generate_branch_answer(
    intent: Intent,
    state: ChatGraphState,
    evidences: list[dict],
) -> str:
    system = _BRANCH_SYSTEM_PROMPTS[intent]
    if evidences and intent != "unclear":
        context = "\n\n".join(
            f"[{evidence.get('source_title') or '정책'}] {evidence.get('snippet', '')}"
            for evidence in evidences
        )
        system = f"{system}\n\n참고 자료:\n{context}"

    messages: list[BaseMessage] = [SystemMessage(content=system)]
    messages.extend(_history_to_lc_messages(state["history"]))
    messages.append(HumanMessage(content=state["user_content"]))

    try:
        response = await _llm().ainvoke(messages)
        content = response.content
        return content if isinstance(content, str) else str(content)
    except Exception:
        logger.exception("Branch answer generation failed; using fallback")
        return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."


async def _branch_with_rag(intent: Intent, state: ChatGraphState) -> dict:
    policies, evidences = await _rag_lookup(state["user_content"])
    content = await _generate_branch_answer(intent, state, evidences)
    action = _INTENT_TO_ACTION[intent]
    return {"assistant_payload": {
        "content": content,
        "user_status": None,
        "sources": [],
        "policies": policies,
        "evidences": evidences,
        "actions": [action] if action else [],
        "disclaimer": True,
    }}


async def _branch_recommend(state: ChatGraphState) -> dict:
    return await _branch_with_rag("recommend", state)


async def _branch_eligibility(state: ChatGraphState) -> dict:
    return await _branch_with_rag("eligibility", state)


async def _branch_compare(state: ChatGraphState) -> dict:
    return await _branch_with_rag("compare", state)


async def _branch_apply(state: ChatGraphState) -> dict:
    return await _branch_with_rag("apply", state)


async def _branch_policy_summary(state: ChatGraphState) -> dict:
    return await _branch_with_rag("policy_summary", state)


async def _branch_unclear(state: ChatGraphState) -> dict:
    content = await _generate_branch_answer("unclear", state, evidences=[])
    return {"assistant_payload": {
        "content": content,
        "user_status": None,
        "sources": [],
        "policies": [],
        "evidences": [],
        "actions": [],
        "disclaimer": False,
    }}


def _route_by_intent(state: ChatGraphState) -> Intent:
    decision = state.get("supervisor_decision")
    if decision is None:
        return "unclear"
    return decision["intent"]


_BRANCH_NODES: dict[Intent, callable] = {
    "recommend": _branch_recommend,
    "eligibility": _branch_eligibility,
    "compare": _branch_compare,
    "apply": _branch_apply,
    "policy_summary": _branch_policy_summary,
    "unclear": _branch_unclear,
}


def _build_graph():
    graph = StateGraph(ChatGraphState)
    graph.add_node("supervisor", _classify_intent)
    for name, node in _BRANCH_NODES.items():
        graph.add_node(name, node)

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        _route_by_intent,
        {name: name for name in _BRANCH_NODES},
    )
    for name in _BRANCH_NODES:
        graph.add_edge(name, END)

    return graph.compile()


chat_supervisor_graph = _build_graph()


__all__ = [
    "ChatGraphState",
    "HistoryMessage",
    "Intent",
    "SupervisorDecision",
    "HISTORY_LIMIT",
    "chat_supervisor_graph",
]

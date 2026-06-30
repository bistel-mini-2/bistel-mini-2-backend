import asyncio
import json
import logging
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.states.chat_state import HistoryMessage
from app.core.config import settings

logger = logging.getLogger(__name__)

# ─── FOLLOW_UP 의도 분류 ────────────────────────────────────────────────────

_FOLLOW_UP_INTENT_SYSTEM = """\
사용자가 정책 지원 가능성 검토 중 추가 정보가 필요한 상태입니다.

정책: {policy_name}
부족한 정보 항목: {follow_up_questions}

사용자의 마지막 메시지 의도를 아래 네 가지 중 하나로 분류하세요.

- recommendation: 이 eligibility와 관계없이 맞춤 정책 추천을 원하는 경우
- eligibility_clarification: "뭐가 부족해?", "왜 확인이 필요해?" 등 부족 정보 자체를 질문하는 경우
- general: 부족한 정보에 대한 실제 답변을 제공하는 경우
- other_intent: 비교, 상세 설명, 신청 방법 등 eligibility 재분석과 무관한 다른 의도
"""


class _FollowUpIntent(BaseModel):
    intent: Literal["recommendation", "eligibility_clarification", "general", "other_intent"]


def _make_follow_up_llm() -> ChatOpenAI:
    kwargs: dict = {"model": "gpt-4o-mini", "temperature": 0}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


_FOLLOW_UP_LLM: ChatOpenAI = _make_follow_up_llm()


async def classify_follow_up_intent(
    follow_up_policy: dict,
    history: list[HistoryMessage],
    user_content: str,
) -> str:
    llm = _FOLLOW_UP_LLM.with_structured_output(_FollowUpIntent)
    system = _FOLLOW_UP_INTENT_SYSTEM.format(
        policy_name=follow_up_policy.get("policy_name") or "해당 정책",
        follow_up_questions=json.dumps(
            follow_up_policy.get("follow_up_questions") or [], ensure_ascii=False
        ),
    )
    messages: list = [SystemMessage(content=system)]
    for msg in history[-6:]:
        role = msg.get("role", "")
        content = msg.get("content") or ""
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=user_content))
    try:
        result = await llm.ainvoke(messages)
        return result.intent
    except Exception:
        logger.exception("FOLLOW_UP intent classification failed; falling back to general")
        return "general"


def find_follow_up_policy(slot: dict | None) -> dict | None:
    for p in (slot or {}).get("recent_policies") or []:
        if p.get("eligibility_status") == "FOLLOW_UP_REQUIRED":
            return p
    return None


def build_eligibility_clarification_text(follow_up_questions: list[dict]) -> str:
    if not follow_up_questions:
        return "지원 가능성을 정확히 분석하려면 추가 정보가 필요해요. 알고 계신 정보를 편하게 입력해 주세요."
    lines = "\n".join(
        f"• {q.get('question_text') or q.get('field_name') or '추가 정보'}"
        for q in follow_up_questions
    )
    return (
        f"지원 가능성을 정확히 분석하려면 아래 정보가 더 필요해요.\n\n"
        f"{lines}\n\n"
        "알고 계신 정보를 편하게 입력해 주시면 다시 분석해드릴게요."
    )


# ─── FOLLOW_UP 답변 매핑 ────────────────────────────────────────────────────

_FOLLOW_UP_ANSWER_SYSTEM = """\
사용자가 지원 가능성 분석 추가 질문에 답변했습니다.

정책: {policy_name}
추가 질문 목록 (index 번호를 그대로 사용하세요):
{follow_up_questions}

사용자 답변을 분석해 각 질문의 index와 답변을 분류하세요.
- yes: 해당 조건을 충족한다고 명시하거나 긍정적으로 답변
- no: 해당 조건을 충족하지 않는다고 명시하거나 부정적으로 답변
- unknown: 언급되지 않았거나 불명확한 경우

모든 질문에 대해 반드시 index와 함께 하나씩 응답하세요.
"""


class _FollowUpAnswerItem(BaseModel):
    index: int
    answer: Literal["yes", "no", "unknown"]


class _FollowUpAnswerMapping(BaseModel):
    mappings: list[_FollowUpAnswerItem]


async def map_follow_up_answers(
    follow_up_policy: dict,
    user_content: str,
) -> list[dict]:
    questions = follow_up_policy.get("follow_up_questions") or []
    question_texts = [
        q.get("question_text") or q.get("field_name") or ""
        for q in questions
        if isinstance(q, dict)
    ]
    question_texts = [q for q in question_texts if q]
    if not question_texts:
        return []
    llm = _FOLLOW_UP_LLM.with_structured_output(_FollowUpAnswerMapping)
    system = _FOLLOW_UP_ANSWER_SYSTEM.format(
        policy_name=follow_up_policy.get("policy_name") or "해당 정책",
        follow_up_questions="\n".join(f"{i}. {q}" for i, q in enumerate(question_texts)),
    )
    try:
        result = await llm.ainvoke([
            SystemMessage(content=system),
            HumanMessage(content=user_content),
        ])
        return [
            {"question": question_texts[item.index], "answer": item.answer}
            for item in result.mappings
            if 0 <= item.index < len(question_texts) and item.answer
        ]
    except Exception:
        logger.exception("FOLLOW_UP answer mapping failed; proceeding with raw_query only")
        return []


async def run_follow_up_eligibility(
    db: AsyncSession,
    user_id: int,
    content: str,
    follow_up_policy: dict,
    manual_confirmations: list[dict] | None = None,
) -> dict | None:
    from app.ai.graphs.eligibility_graph import eligibility_graph_runner
    from app.repositories.ai_request_repository import AiRequestRepository

    policy_slug = follow_up_policy["slug"]
    prev_request_id = follow_up_policy.get("eligibility_request_id")
    source_ref_id = str(prev_request_id) if prev_request_id is not None else None

    base_selected: dict = {}
    raw_query = content
    if prev_request_id is not None:
        prev_req = await AiRequestRepository().find_by_id(db, "eligibility", prev_request_id)
        if prev_req is not None:
            prev_parsed = prev_req.parsed_query_json or {}
            base_selected = dict(prev_parsed.get("selected_conditions") or {})
            if prev_req.raw_query:
                raw_query = f"{prev_req.raw_query}\n{content}"

    if manual_confirmations:
        base_selected["manual_confirmations"] = manual_confirmations

    selected_conditions = base_selected if base_selected else None
    try:
        return await asyncio.wait_for(
            eligibility_graph_runner.run(
                db=db,
                user_id=user_id,
                policy_identifier=policy_slug,
                raw_query=raw_query,
                source_type="CHAT",
                source_ref_id=source_ref_id,
                follow_up_resolved=True,
                selected_conditions=selected_conditions,
            ),
            timeout=60,
        )
    except Exception:
        logger.exception("FOLLOW_UP eligibility re-analysis failed")
        return None


async def attach_similar_policies(db: AsyncSession, state: dict[str, Any]) -> None:
    payload = state.get("assistant_payload") or {}
    primary_slug = next(
        (str(p["slug"]) for p in payload.get("policies", []) if p.get("slug")),
        None,
    )
    if not primary_slug:
        return
    try:
        from app.services.similar_policy_service import SimilarPolicyService

        result = await SimilarPolicyService().find_similar(
            db, policy_slug=primary_slug, limit=3
        )
        payload["similar_policies"] = [
            {
                "policy_id": str(item.policy_id),
                "slug": item.slug,
                "name": item.name,
                "category": item.category,
                "similarity_reason": item.similarity_reason,
            }
            for item in result.items
        ]
    except Exception as exc:
        logger.warning("similar policy attach failed for %s: %s", primary_slug, exc)

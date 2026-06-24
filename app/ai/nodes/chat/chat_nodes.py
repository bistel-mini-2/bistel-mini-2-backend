from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.ai.states.chat_state import (
    ChatGraphState,
    ChatSlot,
    HistoryMessage,
    Intent,
    SlotPolicy,
)
from app.common.ai_status import RequestStatus
from app.common.exceptions import AppException, ErrorCode
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.schemas.ai_request_schema import AiRequestSnapshot
from app.schemas.apply_schema import ApplyPreparationResponse
from app.services.apply_preparation_service import ApplyPreparationService
from app.services.policy_rag_service import PolicyRagService

if TYPE_CHECKING:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService


def _lifecycle_service_class() -> type["AiRequestLifecycleService"]:
    from app.services.ai_request_lifecycle_service import AiRequestLifecycleService

    return AiRequestLifecycleService


logger = logging.getLogger(__name__)


SUPERVISOR_SYSTEM_TEMPLATE = """당신은 임신·출산·육아 정책 챗봇의 Supervisor입니다.
사용자 메시지를 다음 6개 intent 중 하나로 분류하세요.

- recommend: 본인 상황에 맞는 정책 추천 요청 (예: "맞는 정책 알려줘")
- eligibility: 특정 정책의 지원 자격이나 가능성 (예: "나도 받을 수 있어?")
- compare: 정책 2개 비교 (예: "A랑 B 중 뭐가 나아?")
- apply: 신청 방법·필요 서류·기한 (예: "어떻게 신청해?")
- policy_summary: 정책 내용 설명·요약 또는 일반 정책 정보 질의 (예: "이 정책이 뭐야?")
- unclear: 위에 명확히 속하지 않거나 정책과 무관

직전 대화 맥락도 함께 고려해서 결정합니다.

[직전 거론 정책]
{slot_context}

다음 중 하나에 해당하면 resolved_policy_slug 필드에 위 정책의 slug를 정확히 그대로 반환하세요:
1. 사용자가 지시어로 직전 정책을 가리킴: "그 정책", "거기", "방금 그거", "이거" 등
2. 주어가 생략된 후속 질문이 직전 정책 맥락의 연속으로 자연스럽게 해석됨: "신청 기간은?", "필요한 서류는?", "언제까지야?", "어디서 받아?"

새 정책을 명시했거나 슬롯 정보가 비어있거나 정책과 무관한 메시지면 resolved_policy_slug는 null입니다.
슬롯에 정책이 여러 개고 어느 것을 가리키는지 모호하면 첫 번째 정책의 slug를 선택하지 말고 null로 두세요.
"""


_COMMON_SAFETY_RULES = """[안전 안내 — 모든 답변에 적용]
- 정책 수급 가능 여부를 단정짓지 마세요. "받을 수 있습니다", "신청 가능합니다" 같은 확정 표현 대신 "조건에 맞으면", "해당될 수 있어요" 같은 추정 표현을 사용하세요.
- 답변 본문에 면책 문구를 직접 넣지 마세요 (별도 disclaimer 필드로 노출됩니다).
- 정확한 판단·신청 가능 여부는 공식기관(주민센터·복지로 등) 확인이 필요함을 자연스럽게 안내하세요."""


_BASE_BRANCH_PROMPTS: dict[Intent, str] = {
    "policy_summary": """당신은 임신·출산·육아 정책을 안내하는 챗봇입니다.
주어진 정책 문서 발췌(참고 자료)만 근거로 한국어 3~5문장으로 답변하세요.
- 발췌에 없는 내용은 추측하지 마세요.""",
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


_BRANCH_SYSTEM_PROMPTS: dict[Intent, str] = {
    intent: (
        f"{_COMMON_SAFETY_RULES}\n\n{prompt}" if intent != "unclear" else prompt
    )
    for intent, prompt in _BASE_BRANCH_PROMPTS.items()
}


_ASSERTIVE_PHRASES: tuple[str, ...] = (
    "받을 수 있습니다",
    "받을 수 있어요",
    "받으실 수 있습니다",
    "신청 가능합니다",
    "신청하실 수 있습니다",
    "지원받을 수 있습니다",
    "지원받으실 수 있습니다",
    "대상입니다",
    "해당됩니다",
)


def _detect_assertive_phrases(content: str) -> list[str]:
    return [phrase for phrase in _ASSERTIVE_PHRASES if phrase in content]


_INTENT_TO_API_ACTION: dict[Intent, str | None] = {
    "recommend": "recommend",
    "eligibility": "eligibility",
    "compare": "compare",
    "apply": "apply",
    "policy_summary": "chat",
    "unclear": None,
}


_INTENT_TO_ACTION_TYPE: dict[Intent, str | None] = {
    "recommend": "RECOMMENDED",
    "compare": "COMPARED",
    "eligibility": "ELIGIBILITY_TARGET",
    "apply": "APPLY_TARGET",
    "policy_summary": None,
    "unclear": None,
}


_LLM_MODEL = "gpt-4o-mini"
_RAG_TOP_K = 5
_POLICIES_MAX = 3
_EVIDENCES_MAX = 5
_SNIPPET_LIMIT = 300
_RECOMMEND_SOURCE_TYPE = "CHAT"
_RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS = 60
_RECOMMEND_LOCK_TIMEOUT = "5s"
_RECOMMEND_STATEMENT_TIMEOUT = "60s"
_RECOMMEND_FALLBACK_FOLLOW_UP = (
    "맞춤 추천을 위해 정보가 더 필요해요. 맞춤 추천 화면에서 추가로 입력해 주세요."
)
_RECOMMEND_FALLBACK_ERROR = (
    "맞춤 추천을 만드는 중에 문제가 발생했어요. 잠시 후 다시 시도해 주세요."
)
_APPLY_LIFECYCLE_TIMEOUT_SECONDS = 12
_APPLY_LOCK_TIMEOUT = "5s"
_APPLY_STATEMENT_TIMEOUT = "10s"
_APPLY_CHECKLIST_PREVIEW = 5
_EVIDENCE_ROLE_ENUM: frozenset[str] = frozenset(
    {"SUMMARY", "TARGET", "BENEFIT", "APPLICATION", "CAUTION"}
)


def _normalize_evidence_role(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    upper = value.upper()
    return upper if upper in _EVIDENCE_ROLE_ENUM else None


class _IntentDecision(BaseModel):
    intent: Intent = Field(description="사용자 메시지의 의도 분류")
    resolved_policy_slug: str | None = Field(
        default=None,
        description="사용자가 직전 거론 정책을 지시어로 가리키는 경우 그 정책의 slug. 그렇지 않으면 null.",
    )


BRANCH_LLM_TAG = "chat_branch_llm"
_BRANCH_LLM_CONFIG = {"tags": [BRANCH_LLM_TAG]}


def _llm() -> ChatOpenAI:
    kwargs: dict = {"model": _LLM_MODEL, "temperature": 0.2}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


async def _mark_recommendation_failed(request_id: int, error_message: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            await _lifecycle_service_class()().mark_failed(
                db=db,
                request_type="recommendation",
                request_id=request_id,
                error_message=error_message,
            )
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception(
                "failed to mark recommendation request as failed: %s", request_id
            )


def _adapt_recommendation_result(
    result_json: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results = result_json.get("results") or []
    policies: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    seen_chunks: set[Any] = set()
    for item in results[:_POLICIES_MAX]:
        slug = item.get("slug") or item.get("policy_code") or item.get("policy_id")
        if slug:
            policies.append(
                {
                    "policy_id": item.get("policy_id") or slug,
                    "slug": slug,
                    "policy_name": item.get("policy_name") or "",
                    "summary": item.get("summary") or item.get("benefit_summary"),
                    "tag": None,
                    "tagTone": None,
                }
            )
        for evidence in item.get("evidence") or []:
            chunk_id = evidence.get("chunk_id")
            if chunk_id is None or chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            evidences.append(
                {
                    "chunk_id": chunk_id,
                    "snippet": (evidence.get("snippet") or "")[:_SNIPPET_LIMIT],
                    "source_title": evidence.get("source_title")
                    or item.get("policy_name")
                    or "",
                    "source_url": evidence.get("source_url"),
                    "evidence_role": _normalize_evidence_role(
                        evidence.get("evidence_role")
                    ),
                }
            )
            if len(evidences) >= _EVIDENCES_MAX:
                return policies, evidences
    return policies, evidences


def _pick_apply_target(
    policies: list[dict[str, Any]],
) -> tuple[str | None, str | None]:
    for policy in policies:
        slug = policy.get("slug")
        if slug:
            return str(slug), policy.get("policy_name") or None
    return None, None


def _build_apply_card(
    apply_response: ApplyPreparationResponse, policy_name: str | None
) -> dict[str, Any]:
    checklist = [
        {"id": item.id, "label": item.label, "done": item.done}
        for item in apply_response.checklist[:_APPLY_CHECKLIST_PREVIEW]
    ]
    return {
        "policy_id": apply_response.policy_id,
        "policy_name": policy_name or apply_response.policy_id,
        "how_to_apply": apply_response.how_to_apply,
        "apply_period": apply_response.apply_period,
        "contact": apply_response.contact,
        "official_url": apply_response.official_url,
        "checklist": checklist,
        "caution": apply_response.caution,
    }


def _format_apply_card_context(apply_card: dict[str, Any]) -> str:
    parts: list[str] = []
    name = apply_card.get("policy_name")
    if name:
        parts.append(f"- 정책명: {name}")
    if apply_card.get("how_to_apply"):
        parts.append(f"- 신청 방법: {apply_card['how_to_apply']}")
    if apply_card.get("apply_period"):
        parts.append(f"- 신청 기간: {apply_card['apply_period']}")
    if apply_card.get("contact"):
        parts.append(f"- 문의처: {apply_card['contact']}")
    if apply_card.get("official_url"):
        parts.append(f"- 공식 안내: {apply_card['official_url']}")
    checklist = apply_card.get("checklist") or []
    if checklist:
        items = "; ".join(item["label"] for item in checklist if item.get("label"))
        if items:
            parts.append(f"- 체크리스트: {items}")
    if apply_card.get("caution"):
        parts.append(f"- 주의사항: {apply_card['caution']}")
    return "\n".join(parts)


def _format_slot_context(slot: ChatSlot | None) -> str:
    if not slot:
        return "(없음)"
    recent = slot.get("recent_policies") or []
    if not recent:
        return "(없음)"
    return "\n".join(
        f"- {p.get('policy_name') or '(이름 없음)'} "
        f"(slug={p.get('slug')}, action={p.get('last_action')})"
        for p in recent
    )


def _find_slot_policy_by_slug(
    slot: ChatSlot | None, slug: str
) -> SlotPolicy | None:
    if not slot or not slug:
        return None
    for p in slot.get("recent_policies") or []:
        if p.get("slug") == slug:
            return p  # type: ignore[return-value]
    return None


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


class ChatGraphNodes:
    def __init__(
        self,
        rag_service: PolicyRagService | None = None,
        lifecycle_service: AiRequestLifecycleService | None = None,
    ) -> None:
        self.rag_service = rag_service or PolicyRagService()
        self.lifecycle_service = lifecycle_service

    async def supervisor(self, state: ChatGraphState) -> ChatGraphState:
        llm = _llm().with_structured_output(_IntentDecision)
        slot = state.get("slot")
        slot_context = _format_slot_context(slot)
        system_prompt = SUPERVISOR_SYSTEM_TEMPLATE.format(slot_context=slot_context)
        messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
        messages.extend(_history_to_lc_messages(state["history"]))
        messages.append(HumanMessage(content=state["user_content"]))

        resolved_slug: str | None = None
        try:
            decision = await llm.ainvoke(messages)
            intent: Intent = decision.intent
            raw = decision.model_dump_json()
            candidate = decision.resolved_policy_slug
            if candidate and _find_slot_policy_by_slug(slot, candidate):
                resolved_slug = candidate
        except Exception as exc:
            logger.exception("Intent classification failed; falling back to unclear")
            intent = "unclear"
            raw = f"error: {exc}"

        return {
            **state,
            "supervisor_decision": {
                "intent": intent,
                "raw": raw,
                "resolved_policy_slug": resolved_slug,
            },
        }

    async def branch_recommend(self, state: ChatGraphState) -> ChatGraphState:
        snapshot = await self._run_recommendation_lifecycle(
            user_id=state["user_id"],
            user_content=state["user_content"],
        )
        if snapshot is None:
            return await self._recommend_fallback(state, _RECOMMEND_FALLBACK_ERROR)
        if snapshot.status == RequestStatus.FOLLOW_UP_REQUIRED:
            return await self._recommend_fallback(
                state, _RECOMMEND_FALLBACK_FOLLOW_UP
            )
        if snapshot.status != RequestStatus.COMPLETED:
            return await self._recommend_fallback(state, _RECOMMEND_FALLBACK_ERROR)
        policies, evidences = _adapt_recommendation_result(snapshot.result_json)
        content = await self._generate_branch_answer("recommend", state, evidences)
        return {
            **state,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    async def branch_eligibility(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        if resolved_slug:
            return await self._branch_with_slot("eligibility", state, resolved_slug)
        logger.info(
            "chat_slot_resolved",
            extra={"intent": "eligibility", "slot_used": False, "rag_skipped": False},
        )
        return await self._branch_with_rag("eligibility", state)

    async def branch_compare(self, state: ChatGraphState) -> ChatGraphState:
        return await self._branch_with_rag("compare", state)

    async def branch_apply(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {}
        resolved_slug = decision.get("resolved_policy_slug")
        slot_policy = _find_slot_policy_by_slug(state.get("slot"), resolved_slug or "")
        if resolved_slug and slot_policy:
            slug = resolved_slug
            policy_name = slot_policy.get("policy_name") or None
            evidences: list[dict[str, Any]] = []
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "apply", "slot_used": True, "rag_skipped": True},
            )
        else:
            policies, evidences = await self._rag_lookup(state["user_content"])
            slug, policy_name = _pick_apply_target(policies)
            logger.info(
                "chat_slot_resolved",
                extra={"intent": "apply", "slot_used": False, "rag_skipped": False},
            )

        if slug is None:
            content = await self._generate_branch_answer("apply", state, evidences)
            return {
                **state,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        apply_response = await self._run_apply_preparation(
            user_id=state["user_id"],
            policy_slug=slug,
        )
        if apply_response is None:
            content = await self._generate_branch_answer("apply", state, evidences)
            return {
                **state,
                "branch_content": content,
                "branch_policies": [],
                "branch_evidences": evidences,
                "branch_apply_card": None,
            }

        apply_card = _build_apply_card(apply_response, policy_name)
        apply_policies = [
            {
                "policy_id": None,
                "slug": slug,
                "policy_name": policy_name or "",
                "summary": None,
                "tag": None,
                "tagTone": None,
            }
        ]
        content = await self._generate_apply_answer(state, evidences, apply_card)
        return {
            **state,
            "branch_content": content,
            "branch_policies": apply_policies,
            "branch_evidences": evidences,
            "branch_apply_card": apply_card,
        }

    async def branch_policy_summary(self, state: ChatGraphState) -> ChatGraphState:
        return await self._branch_with_rag("policy_summary", state)

    async def branch_unclear(self, state: ChatGraphState) -> ChatGraphState:
        content = await self._generate_branch_answer("unclear", state, evidences=[])
        return {
            **state,
            "branch_content": content,
            "branch_policies": [],
            "branch_evidences": [],
        }

    async def assistant_payload_build(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": "missing"}
        intent: Intent = decision["intent"]
        api_action = _INTENT_TO_API_ACTION.get(intent)
        content = state.get("branch_content") or ""
        if intent != "unclear":
            assertive = _detect_assertive_phrases(content)
            if assertive:
                logger.warning(
                    "chat answer contains assertive phrases despite safety prompt",
                    extra={"intent": intent, "phrases": assertive},
                )
        payload = {
            "content": content,
            "user_status": None,
            "sources": [],
            "policies": state.get("branch_policies", []),
            "evidences": state.get("branch_evidences", []),
            "actions": [api_action] if api_action else [],
            "apply_card": state.get("branch_apply_card"),
            "disclaimer": intent != "unclear",
        }
        return {**state, "assistant_payload": payload}

    async def evidence_extract(self, state: ChatGraphState) -> ChatGraphState:
        evidences = state.get("branch_evidences", [])
        to_save = [
            {
                "chunk_id": e["chunk_id"],
                "snippet": e.get("snippet"),
                "evidence_role": e.get("evidence_role"),
            }
            for e in evidences
            if e.get("chunk_id") is not None
        ]
        return {**state, "evidences_to_save": to_save}

    async def policy_link_extract(self, state: ChatGraphState) -> ChatGraphState:
        decision = state.get("supervisor_decision") or {"intent": "unclear", "raw": ""}
        intent: Intent = decision["intent"]
        action_type = _INTENT_TO_ACTION_TYPE.get(intent)
        if action_type is None:
            return {**state, "policy_links_to_save": []}
        links = [
            {"policy_slug": p["slug"], "action_type": action_type}
            for p in state.get("branch_policies", [])
            if p.get("slug")
        ]
        return {**state, "policy_links_to_save": links}

    async def _run_recommendation_lifecycle(
        self,
        user_id: int,
        user_content: str,
    ) -> AiRequestSnapshot | None:
        request_id: int | None = None
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_RECOMMEND_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_RECOMMEND_STATEMENT_TIMEOUT}'"
                        )
                    )
                    lifecycle = (
                        self.lifecycle_service or _lifecycle_service_class()()
                    )
                    created = await lifecycle.create_request(
                        db=db,
                        user_id=user_id,
                        request_type="recommendation",
                        source_type=_RECOMMEND_SOURCE_TYPE,
                        raw_query=user_content,
                    )
                    request_id = int(created.request_id)
                    await lifecycle.mark_processing(
                        db=db,
                        request_type="recommendation",
                        request_id=request_id,
                    )
                    snapshot = await asyncio.wait_for(
                        lifecycle.process_condition_request(
                            db=db,
                            request_type="recommendation",
                            request_id=request_id,
                        ),
                        timeout=_RECOMMEND_LIFECYCLE_TIMEOUT_SECONDS,
                    )
                    await db.commit()
                    return snapshot
                except Exception:
                    await db.rollback()
                    raise
        except Exception as exc:
            logger.exception("chat branch_recommend lifecycle failed")
            if request_id is not None:
                await _mark_recommendation_failed(request_id, str(exc))
            return None

    async def _recommend_fallback(
        self,
        state: ChatGraphState,
        message: str,
    ) -> ChatGraphState:
        return {
            **state,
            "branch_content": message,
            "branch_policies": [],
            "branch_evidences": [],
        }

    async def _run_apply_preparation(
        self,
        user_id: int,
        policy_slug: str,
    ) -> ApplyPreparationResponse | None:
        try:
            async with AsyncSessionLocal() as db:
                try:
                    await db.execute(
                        text(f"SET LOCAL lock_timeout = '{_APPLY_LOCK_TIMEOUT}'")
                    )
                    await db.execute(
                        text(
                            f"SET LOCAL statement_timeout = "
                            f"'{_APPLY_STATEMENT_TIMEOUT}'"
                        )
                    )
                    response = await asyncio.wait_for(
                        ApplyPreparationService.get(
                            db=db,
                            user_id=user_id,
                            policy_slug=policy_slug,
                        ),
                        timeout=_APPLY_LIFECYCLE_TIMEOUT_SECONDS,
                    )
                    await db.commit()
                    return response
                except Exception:
                    await db.rollback()
                    raise
        except AppException as exc:
            if exc.code != ErrorCode.POLICY_NOT_FOUND:
                raise
            logger.info(
                "chat branch_apply preview skipped: %s", exc.message
            )
            return None
        except Exception:
            logger.exception("chat branch_apply preview failed")
            return None

    async def _generate_apply_answer(
        self,
        state: ChatGraphState,
        evidences: list[dict],
        apply_card: dict,
    ) -> str:
        system = _BRANCH_SYSTEM_PROMPTS["apply"]
        apply_context = _format_apply_card_context(apply_card)
        if apply_context:
            system = f"{system}\n\n신청 정보:\n{apply_context}"
        if evidences:
            rag_context = "\n\n".join(
                f"[{evidence.get('source_title') or '정책'}] {evidence.get('snippet', '')}"
                for evidence in evidences
            )
            system = f"{system}\n\n참고 자료:\n{rag_context}"

        messages: list[BaseMessage] = [SystemMessage(content=system)]
        messages.extend(_history_to_lc_messages(state["history"]))
        messages.append(HumanMessage(content=state["user_content"]))
        try:
            response = await _llm().ainvoke(messages, config=_BRANCH_LLM_CONFIG)
            content = response.content
            return content if isinstance(content, str) else str(content)
        except Exception:
            logger.exception("Apply branch answer generation failed; using fallback")
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."

    async def _branch_with_rag(
        self,
        intent: Intent,
        state: ChatGraphState,
    ) -> ChatGraphState:
        policies, evidences = await self._rag_lookup(state["user_content"])
        content = await self._generate_branch_answer(intent, state, evidences)
        return {
            **state,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": evidences,
        }

    async def _branch_with_slot(
        self,
        intent: Intent,
        state: ChatGraphState,
        slug: str,
    ) -> ChatGraphState:
        slot_policy = _find_slot_policy_by_slug(state.get("slot"), slug)
        policy_name = (slot_policy or {}).get("policy_name") or ""
        policies = [
            {
                "policy_id": None,
                "slug": slug,
                "policy_name": policy_name,
                "summary": None,
                "tag": None,
                "tagTone": None,
            }
        ]
        content = await self._generate_branch_answer(intent, state, evidences=[])
        logger.info(
            "chat_slot_resolved",
            extra={"intent": intent, "slot_used": True, "rag_skipped": True},
        )
        return {
            **state,
            "branch_content": content,
            "branch_policies": policies,
            "branch_evidences": [],
        }

    async def _rag_lookup(
        self, query: str
    ) -> tuple[list[dict], list[dict]]:
        try:
            result = await self.rag_service.search(query=query, k=_RAG_TOP_K)
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
                "chunk_id": chunk.chunk_id,
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
        self,
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
            response = await _llm().ainvoke(messages, config=_BRANCH_LLM_CONFIG)
            content = response.content
            return content if isinstance(content, str) else str(content)
        except Exception:
            logger.exception("Branch answer generation failed; using fallback")
            return "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요."

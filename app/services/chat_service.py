import asyncio
import json
import logging
from collections.abc import AsyncIterator
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import status
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.chat_supervisor_graph import (
    HISTORY_LIMIT,
    chat_supervisor_graph,
)
from app.ai.nodes.chat.chat_nodes import BRANCH_LLM_TAG
from app.ai.states.chat_state import HistoryMessage
from app.core.config import settings
from app.common.exceptions import AppException, ErrorCode
from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.repositories.policy_repository import PolicyRepository
from app.schemas.chat_schema import (
    ApplyCard,
    AssistantMessage,
    AssistantMessageEvidence,
    AssistantMessagePolicy,
    ChatSessionBulkDeleteResponse,
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionDeleteResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
    ChatSessionTitleUpdateResponse,
)
from app.services import chat_cancel_registry
from app.services.chat_title_service import assign_title_if_missing


logger = logging.getLogger(f"{__name__}.ChatService")


class ChatService:
    @staticmethod
    async def create_session(
        db: AsyncSession, user_id: int, title: str | None
    ) -> ChatSessionCreateResponse:
        session = await ChatRepository.save_session(
            db,
            ChatSession(user_id=user_id, title=title),
        )
        return ChatSessionCreateResponse(
            chat_session_id=str(session.chat_session_id),
            session_status=session.session_status,
        )

    @staticmethod
    async def list_sessions(
        db: AsyncSession, user_id: int
    ) -> ChatSessionListResponse:
        sessions = await ChatRepository.find_sessions_by_user(db, user_id)
        return ChatSessionListResponse(
            sessions=[
                ChatSessionListItem(
                    chat_session_id=str(session.chat_session_id),
                    title=session.title,
                    session_status=session.session_status,
                    last_message_at=session.last_message_at,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                for session in sessions
            ]
        )

    @staticmethod
    async def update_session_title(
        db: AsyncSession,
        user_id: int,
        chat_session_id: int,
        title: str,
    ) -> ChatSessionTitleUpdateResponse:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        updated_at = await ChatRepository.update_title(
            db, session.chat_session_id, title
        )
        return ChatSessionTitleUpdateResponse(
            chat_session_id=str(session.chat_session_id),
            title=title,
            updated_at=updated_at,
        )

    @staticmethod
    async def delete_session(
        db: AsyncSession,
        user_id: int,
        chat_session_id: int,
    ) -> ChatSessionDeleteResponse:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        chat_cancel_registry.cancel(session.chat_session_id)
        await ChatRepository.delete_session(db, session)
        await db.commit()
        return ChatSessionDeleteResponse(
            chat_session_id=str(session.chat_session_id),
            deleted=True,
        )

    @staticmethod
    async def bulk_delete_sessions(
        db: AsyncSession,
        user_id: int,
        chat_session_ids: list[int],
    ) -> ChatSessionBulkDeleteResponse:
        ids = list(dict.fromkeys(chat_session_ids))
        sessions = await ChatRepository.find_sessions_by_user_and_ids(
            db, user_id, ids
        )
        found_ids = {session.chat_session_id for session in sessions}
        if len(found_ids) != len(ids):
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Chat session not found",
            )

        chat_cancel_registry.cancel_many(ids)
        deleted_count = await ChatRepository.delete_sessions_by_ids(db, ids)
        if deleted_count != len(ids):
            await db.rollback()
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Chat session not found",
            )
        await db.commit()
        return ChatSessionBulkDeleteResponse(
            deleted_count=deleted_count,
            deleted_session_ids=[str(chat_session_id) for chat_session_id in ids],
        )

    @staticmethod
    async def list_messages(
        db: AsyncSession, user_id: int, chat_session_id: int
    ) -> ChatMessageListResponse:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        messages = await ChatRepository.find_messages_by_session(
            db, session.chat_session_id
        )
        items = await _build_message_items(db, messages)
        return ChatMessageListResponse(
            chat_session_id=str(session.chat_session_id),
            messages=items,
        )

    @staticmethod
    async def send_message(
        db: AsyncSession,
        user_id: int,
        chat_session_id: int,
        content: str,
    ) -> ChatMessageSendResponse:
        cancel_event = chat_cancel_registry.register(chat_session_id)
        try:
            session = await _get_owned_session_or_raise(db, user_id, chat_session_id)

            history = await _load_history(db, session.chat_session_id)
            is_first_message = not history and not session.title
            recent_assistant_policy = await ChatRepository.find_recent_assistant_policy(
                db, session.chat_session_id
            )
            user_message = await _save_user_message(
                db, session.chat_session_id, content
            )
            await db.commit()

            graph_result = await _run_supervisor_graph(
                user_id=user_id,
                user_content=content,
                history=history,
                slot=session.slot_json or {},
                recent_assistant_policy=recent_assistant_policy,
            )

            if await _is_cancelled_or_deleted(
                db, session.chat_session_id, cancel_event
            ):
                await db.rollback()
                raise AppException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code=ErrorCode.NOT_FOUND,
                    message="Chat session not found",
                )

            assistant_message, assistant_response = await _persist_assistant_outputs(
                db,
                session_id=session.chat_session_id,
                user_message_id=user_message.chat_message_id,
                graph_result=graph_result,
                current_slot=session.slot_json or {},
            )

            await ChatRepository.update_last_message_at(
                db, session.chat_session_id, datetime.utcnow()
            )

            if is_first_message:
                _schedule_title_generation(session.chat_session_id, content)

            return ChatMessageSendResponse(
                chat_session_id=str(session.chat_session_id),
                user_message_id=str(user_message.chat_message_id),
                assistant_message=assistant_response,
            )
        finally:
            chat_cancel_registry.unregister(chat_session_id, cancel_event)

    @staticmethod
    async def ensure_owned_session(
        db: AsyncSession, user_id: int, chat_session_id: int
    ) -> ChatSession:
        return await _get_owned_session_or_raise(db, user_id, chat_session_id)

    @staticmethod
    async def send_message_stream(
        db: AsyncSession,
        session: ChatSession,
        content: str,
    ) -> AsyncIterator[str]:
        cancel_event = chat_cancel_registry.register(session.chat_session_id)
        try:
            history = await _load_history(db, session.chat_session_id)
            is_first_message = not history and not session.title
            recent_assistant_policy = await ChatRepository.find_recent_assistant_policy(
                db, session.chat_session_id
            )
            slot = session.slot_json or {}

            # --- FOLLOW_UP 경로: eligibility 재질문 상태가 슬롯에 있으면 3-way 분류 ---
            follow_up_policy = _find_follow_up_policy(slot)
            if follow_up_policy:
                follow_up_intent = await _classify_follow_up_intent(
                    follow_up_policy, history, content
                )
                yield _sse_event({"type": "intent", "intent": follow_up_intent})

                if follow_up_intent == "eligibility_clarification":
                    clarification_text = _build_eligibility_clarification_text(
                        follow_up_policy.get("follow_up_questions") or []
                    )
                    yield _sse_event({"type": "token", "delta": clarification_text})
                    try:
                        user_message = await _save_user_message(
                            db, session.chat_session_id, content
                        )
                        minimal_result: dict[str, Any] = {
                            "assistant_payload": {
                                "content": clarification_text,
                                "policies": [],
                                "evidences": [],
                                "actions": [],
                                "disclaimer": False,
                            },
                            "supervisor_decision": {
                                "intent": "unclear",
                                "raw": "eligibility_clarification",
                            },
                        }
                        _, assistant_response = await _persist_assistant_outputs(
                            db,
                            session_id=session.chat_session_id,
                            user_message_id=user_message.chat_message_id,
                            graph_result=minimal_result,
                            current_slot=slot,
                        )
                        await ChatRepository.update_last_message_at(
                            db, session.chat_session_id, datetime.utcnow()
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        logger.exception("Persisting eligibility_clarification message failed")
                        yield _sse_event({
                            "type": "error",
                            "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                            "message": "답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        })
                        return
                    response = ChatMessageSendResponse(
                        chat_session_id=str(session.chat_session_id),
                        user_message_id=str(user_message.chat_message_id),
                        assistant_message=assistant_response,
                    )
                    yield _sse_event({"type": "done", "payload": response.model_dump(mode="json")})
                    return
                # recommendation / general → 그래프로 이어서 처리 (intent 이벤트는 이미 발행됨)

            graph_state = {
                "user_id": session.user_id,
                "user_content": content,
                "history": history,
                "slot": slot,
                "recent_assistant_policy": recent_assistant_policy,
            }

            final_state: dict[str, Any] = {}
            stream_failed = False
            intent_emitted = follow_up_policy is not None  # FOLLOW_UP 경로는 이미 발행
            try:
                async for event in chat_supervisor_graph.astream_events(
                    graph_state, version="v2"
                ):
                    if cancel_event.is_set():
                        await db.rollback()
                        yield _sse_event(_cancelled_event_payload())
                        return

                    kind = event.get("event")
                    if kind == "on_chat_model_stream":
                        if BRANCH_LLM_TAG not in (event.get("tags") or []):
                            continue
                        delta = _extract_token_text(event.get("data", {}).get("chunk"))
                        if delta:
                            yield _sse_event({"type": "token", "delta": delta})
                    elif kind == "on_chain_end":
                        output = event.get("data", {}).get("output")
                        if isinstance(output, dict):
                            final_state.update(output)
                            if not intent_emitted and "supervisor_decision" in output:
                                sv_intent = (output["supervisor_decision"] or {}).get(
                                    "intent", "unclear"
                                )
                                yield _sse_event(
                                    {"type": "intent", "intent": _INTENT_TO_SSE[sv_intent]}
                                )
                                intent_emitted = True
            except Exception:
                stream_failed = True
                logger.exception("Chat supervisor graph streaming failed")

            if stream_failed or not final_state.get("assistant_payload"):
                yield _sse_event({
                    "type": "error",
                    "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                    "message": "답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                })
                return

            if await _is_cancelled_or_deleted(
                db, session.chat_session_id, cancel_event
            ):
                await db.rollback()
                yield _sse_event(_cancelled_event_payload())
                return

            try:
                user_message = await _save_user_message(
                    db, session.chat_session_id, content
                )
                _, assistant_response = await _persist_assistant_outputs(
                    db,
                    session_id=session.chat_session_id,
                    user_message_id=user_message.chat_message_id,
                    graph_result=final_state,
                    current_slot=slot,
                )
                await ChatRepository.update_last_message_at(
                    db, session.chat_session_id, datetime.utcnow()
                )
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("Persisting chat messages failed during stream")
                yield _sse_event({
                    "type": "error",
                    "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                    "message": "답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                })
                return

            if is_first_message:
                _schedule_title_generation(session.chat_session_id, content)

            response = ChatMessageSendResponse(
                chat_session_id=str(session.chat_session_id),
                user_message_id=str(user_message.chat_message_id),
                assistant_message=assistant_response,
            )
            yield _sse_event({
                "type": "done",
                "payload": response.model_dump(mode="json"),
            })
        finally:
            chat_cancel_registry.unregister(session.chat_session_id, cancel_event)


# --- helpers ---------------------------------------------------------------


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# --- FOLLOW_UP intent classification --------------------------------------

_FOLLOW_UP_INTENT_SYSTEM = """\
사용자가 정책 지원 가능성 검토 중 추가 정보가 필요한 상태입니다.

정책: {policy_name}
부족한 정보 항목: {follow_up_questions}

사용자의 마지막 메시지 의도를 아래 세 가지 중 하나로 분류하세요.

- recommendation: 이 eligibility와 관계없이 맞춤 정책 추천을 원하는 경우
- eligibility_clarification: "뭐가 부족해?", "왜 확인이 필요해?" 등 부족 정보 자체를 질문하는 경우
- general: 위 두 경우 외 모두 (부족한 정보를 실제로 제공하는 답변 포함)
"""


class _FollowUpIntent(BaseModel):
    intent: Literal["recommendation", "eligibility_clarification", "general"]


def _follow_up_llm() -> ChatOpenAI:
    kwargs: dict = {"model": "gpt-4o-mini", "temperature": 0}
    if settings.openai_api_key:
        kwargs["api_key"] = settings.openai_api_key
    return ChatOpenAI(**kwargs)


async def _classify_follow_up_intent(
    follow_up_policy: dict,
    history: list[HistoryMessage],
    user_content: str,
) -> str:
    llm = _follow_up_llm().with_structured_output(_FollowUpIntent)
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
            from langchain_core.messages import AIMessage
            messages.append(AIMessage(content=content))
    messages.append(HumanMessage(content=user_content))
    try:
        result = await llm.ainvoke(messages)
        return result.intent
    except Exception:
        logger.exception("FOLLOW_UP intent classification failed; falling back to general")
        return "general"


def _find_follow_up_policy(slot: dict | None) -> dict | None:
    for p in (slot or {}).get("recent_policies") or []:
        if p.get("eligibility_status") == "FOLLOW_UP_REQUIRED":
            return p
    return None


def _build_eligibility_clarification_text(follow_up_questions: list[dict]) -> str:
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


_INTENT_TO_SSE: dict[str, str] = defaultdict(lambda: "general", {"recommend": "recommendation"})  # type: ignore[assignment]


def _cancelled_event_payload() -> dict[str, str]:
    return {
        "type": "error",
        "code": ErrorCode.NOT_FOUND.value,
        "message": "삭제된 채팅 세션입니다.",
    }


async def _is_cancelled_or_deleted(
    db: AsyncSession,
    chat_session_id: int,
    cancel_event: asyncio.Event,
) -> bool:
    if cancel_event.is_set():
        return True
    return not await ChatRepository.session_exists(db, chat_session_id)


def _extract_token_text(chunk: Any) -> str:
    if chunk is None:
        return ""
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _schedule_title_generation(chat_session_id: int, user_content: str) -> None:
    asyncio.create_task(assign_title_if_missing(chat_session_id, user_content))


async def _get_owned_session_or_raise(
    db: AsyncSession, user_id: int, chat_session_id: int
) -> ChatSession:
    session = await ChatRepository.find_session_by_id(db, chat_session_id)
    if session is None or session.user_id != user_id:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            code=ErrorCode.NOT_FOUND,
            message="Chat session not found",
        )
    return session


async def _load_history(db: AsyncSession, chat_session_id: int) -> list[dict]:
    history_models = await ChatRepository.find_recent_messages(
        db, chat_session_id, limit=HISTORY_LIMIT
    )
    return [
        {"role": message.role, "content": message.content}
        for message in history_models
        if message.content
    ]


async def _save_user_message(
    db: AsyncSession, chat_session_id: int, content: str
) -> ChatMessage:
    sequence_no = await ChatRepository.next_sequence_no(db, chat_session_id)
    return await ChatRepository.save_message(
        db,
        ChatMessage(
            chat_session_id=chat_session_id,
            role="user",
            message_type="TEXT",
            content=content,
            sequence_no=sequence_no,
        ),
    )


async def _run_supervisor_graph(
    *,
    user_id: int,
    user_content: str,
    history: list[dict],
    slot: dict | None = None,
    recent_assistant_policy: dict | None = None,
) -> dict:
    graph_state = {
        "user_id": user_id,
        "user_content": user_content,
        "history": history,
        "slot": slot or {},
        "recent_assistant_policy": recent_assistant_policy,
    }
    try:
        return await chat_supervisor_graph.ainvoke(graph_state)
    except Exception:
        logger.exception("Chat supervisor graph failed")
        return {
            "assistant_payload": _fallback_payload(),
            "supervisor_decision": {"intent": "unclear", "raw": "graph_error"},
            "evidences_to_save": [],
            "policy_links_to_save": [],
        }


async def _persist_assistant_outputs(
    db: AsyncSession,
    *,
    session_id: int,
    user_message_id: int,
    graph_result: dict,
    current_slot: dict | None = None,
) -> tuple[ChatMessage, AssistantMessage]:
    payload = graph_result.get("assistant_payload") or _fallback_payload()
    decision = graph_result.get("supervisor_decision") or {
        "intent": "unclear",
        "raw": "missing",
    }
    evidences_to_save = graph_result.get("evidences_to_save", [])
    policy_links_to_save = graph_result.get("policy_links_to_save", [])

    assistant_sequence = await ChatRepository.next_sequence_no(db, session_id)
    structured_json = _build_structured_json(decision, payload)
    assistant_message = await ChatRepository.save_message(
        db,
        ChatMessage(
            chat_session_id=session_id,
            parent_message_id=user_message_id,
            role="assistant",
            message_type="TEXT",
            content=payload.get("content"),
            structured_json=structured_json,
            sequence_no=assistant_sequence,
        ),
    )

    slug_to_policy_id = await _resolve_policy_ids(db, policy_links_to_save)
    await ChatRepository.bulk_save_message_policies(
        db,
        _build_policy_link_rows(
            assistant_message.chat_message_id,
            policy_links_to_save,
            slug_to_policy_id,
        ),
    )
    await ChatRepository.bulk_save_message_evidences(
        db,
        _build_evidence_rows(assistant_message.chat_message_id, evidences_to_save),
    )

    next_slot = _build_next_slot(
        current_slot=current_slot or {},
        policy_links=policy_links_to_save,
        branch_policies=payload.get("policies", []),
        slug_to_policy_id=slug_to_policy_id,
        profile=graph_result.get("profile"),
        pending=graph_result.get("pending"),
        eligibility_slot_update=graph_result.get("eligibility_slot_update"),
    )
    if next_slot is not None:
        await ChatRepository.update_session_slot(db, session_id, next_slot)

    response = _build_assistant_response(
        assistant_message,
        payload,
        policy_links_to_save,
        slug_to_policy_id,
    )
    return assistant_message, response


_SLOT_MAX_POLICIES = 3


def _build_next_slot(
    *,
    current_slot: dict,
    policy_links: list[dict],
    branch_policies: list[dict],
    slug_to_policy_id: dict[str, int],
    profile: dict | None = None,
    pending: dict | None = None,
    eligibility_slot_update: dict | None = None,
) -> dict | None:
    slug_to_action: dict[str, str] = {}
    for link in policy_links:
        slug = link.get("policy_slug")
        action = link.get("action_type")
        if slug and action and slug not in slug_to_action:
            slug_to_action[slug] = action

    slug_to_name: dict[str, str] = {}
    for policy in branch_policies:
        slug = policy.get("slug")
        if slug and slug not in slug_to_name:
            slug_to_name[slug] = policy.get("policy_name") or ""

    new_entries: list[dict] = []
    seen: set[str] = set()
    for policy in branch_policies:
        slug = policy.get("slug")
        if not slug or slug in seen:
            continue
        policy_id = slug_to_policy_id.get(slug)
        action = slug_to_action.get(slug)
        if policy_id is None or action is None:
            continue
        seen.add(slug)
        new_entries.append({
            "policy_id": policy_id,
            "slug": slug,
            "policy_name": slug_to_name.get(slug) or "",
            "last_action": action,
        })

    # 저장할 게 아무것도 없으면(새 정책·프로필·pending·eligibility 업데이트 모두 없음) 생략.
    if not new_entries and profile is None and pending is None and not eligibility_slot_update:
        return None

    if new_entries:
        existing = list(current_slot.get("recent_policies") or [])
        merged: list[dict] = list(new_entries)
        for entry in existing:
            slug = entry.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            merged.append(entry)
            if len(merged) >= _SLOT_MAX_POLICIES:
                break
        recent_policies = merged[:_SLOT_MAX_POLICIES]
    else:
        # 새 정책이 없으면 기존 목록 유지 (프로필/pending만 갱신).
        recent_policies = list(current_slot.get("recent_policies") or [])

    # eligibility FOLLOW_UP 결과를 해당 정책 슬롯에 반영
    if eligibility_slot_update:
        update_slug = eligibility_slot_update.get("slug")
        recent_policies = [
            {
                **p,
                "eligibility_request_id": eligibility_slot_update.get("eligibility_request_id"),
                "follow_up_questions": eligibility_slot_update.get("follow_up_questions"),
                "eligibility_status": eligibility_slot_update.get("eligibility_status"),
            }
            if p.get("slug") == update_slug
            else p
            for p in recent_policies
        ]

    # profile/pending은 그래프가 값을 주면 그 값을, 안 주면 기존 값을 유지.
    # (pending은 명시적으로 None을 주면 "이어받기 종료"로 해석되어 비워진다.)
    next_profile = (
        profile if profile is not None else current_slot.get("profile") or {}
    )

    return {
        "recent_policies": recent_policies,
        "profile": next_profile,
        "pending": pending,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


async def _resolve_policy_ids(
    db: AsyncSession, policy_links: list[dict]
) -> dict[str, int]:
    slugs = [link["policy_slug"] for link in policy_links if link.get("policy_slug")]
    if not slugs:
        return {}
    slug_to_id = await PolicyRepository.find_ids_by_codes(db, list(set(slugs)))
    missing = [s for s in slugs if s not in slug_to_id]
    if missing:
        logger.warning("Skipping policy links for unknown slugs: %s", missing)
    return slug_to_id


def _build_structured_json(decision: dict, payload: dict) -> dict:
    return {
        "_supervisor": decision,
        "user_status": payload.get("user_status"),
        "easy_summary": payload.get("easy_summary"),
        "key_points": payload.get("key_points", []),
        "sources": payload.get("sources", []),
        "policies": payload.get("policies", []),
        "actions": payload.get("actions", []),
        "apply_card": payload.get("apply_card"),
        "disclaimer": payload.get("disclaimer"),
        "slot_request": payload.get("slot_request"),
        "profile_confirm": payload.get("profile_confirm"),
    }


def _build_policy_link_rows(
    chat_message_id: int,
    policy_links: list[dict],
    slug_to_id: dict[str, int],
) -> list[dict]:
    rows: list[dict] = []
    for link in policy_links:
        slug = link.get("policy_slug")
        policy_id = slug_to_id.get(slug) if slug else None
        if policy_id is None:
            continue
        rows.append({
            "chat_message_id": chat_message_id,
            "policy_id": policy_id,
            "action_type": link["action_type"],
        })
    return rows


def _build_evidence_rows(
    chat_message_id: int, evidences: list[dict]
) -> list[dict]:
    rows: list[dict] = []
    for ev in evidences:
        chunk_id = ev.get("chunk_id")
        if chunk_id is None:
            continue
        rows.append({
            "chat_message_id": chat_message_id,
            "chunk_id": chunk_id,
            "snippet": ev.get("snippet"),
            "evidence_role": ev.get("evidence_role"),
        })
    return rows


def _build_assistant_response(
    assistant_message: ChatMessage,
    payload: dict,
    policy_links: list[dict],
    slug_to_id: dict[str, int],
) -> AssistantMessage:
    slug_to_action = {
        link["policy_slug"]: link["action_type"]
        for link in policy_links
        if link.get("policy_slug") in slug_to_id
    }
    policies = [
        AssistantMessagePolicy(
            policy_id=str(slug_to_id[p["slug"]]) if p.get("slug") in slug_to_id else p["policy_id"],
            slug=p["slug"],
            policy_name=p["policy_name"],
            summary=p.get("summary"),
            tag=p.get("tag"),
            tagTone=p.get("tagTone"),
            action_type=slug_to_action.get(p.get("slug")),
            recommendation_request_id=(
                str(p.get("recommendation_request_id"))
                if p.get("recommendation_request_id") is not None
                else None
            ),
            source_ref_id=(
                str(p.get("source_ref_id"))
                if p.get("source_ref_id") is not None
                else None
            ),
            selected_conditions=p.get("selected_conditions"),
            merged_condition_json=p.get("merged_condition_json"),
        )
        for p in payload.get("policies", [])
    ]
    evidences = [
        AssistantMessageEvidence(
            chunk_id=str(ev["chunk_id"]) if ev.get("chunk_id") is not None else None,
            snippet=ev.get("snippet", ""),
            source_title=ev.get("source_title"),
            source_url=ev.get("source_url"),
            evidence_role=ev.get("evidence_role"),
        )
        for ev in payload.get("evidences", [])
    ]
    apply_card_payload = payload.get("apply_card")
    apply_card = ApplyCard(**apply_card_payload) if apply_card_payload else None
    return AssistantMessage(
        chat_message_id=str(assistant_message.chat_message_id),
        content=payload.get("content") or "",
        user_status=payload.get("user_status"),
        easy_summary=payload.get("easy_summary"),
        key_points=payload.get("key_points", []),
        sources=payload.get("sources", []),
        policies=policies,
        actions=payload.get("actions", []),
        evidences=evidences,
        apply_card=apply_card,
        disclaimer=payload.get("disclaimer"),
        slot_request=payload.get("slot_request"),
        profile_confirm=payload.get("profile_confirm"),
    )


async def _build_message_items(
    db: AsyncSession, messages: list[ChatMessage]
) -> list[ChatMessageItem]:
    if not messages:
        return []
    message_ids = [m.chat_message_id for m in messages]
    policies_by_msg = await ChatRepository.find_policies_by_message_ids(db, message_ids)
    evidences_by_msg = await ChatRepository.find_evidences_by_message_ids(db, message_ids)
    return [
        _to_message_item(
            message,
            policies=policies_by_msg.get(message.chat_message_id, []),
            evidences=evidences_by_msg.get(message.chat_message_id, []),
        )
        for message in messages
    ]


def _to_message_item(
    message: ChatMessage,
    *,
    policies: list[dict],
    evidences: list[dict],
) -> ChatMessageItem:
    meta = _unwrap_message_meta(message.structured_json)
    # DB 정책이 없으면 structured_json에 캐시된 원본 payload 정책으로 fallback
    meta_policies = meta.pop("policies", [])
    if policies:
        meta_by_slug = {
            policy.get("slug"): policy
            for policy in meta_policies
            if policy.get("slug")
        }
        meta_by_id = {
            str(policy.get("policy_id")): policy
            for policy in meta_policies
            if policy.get("policy_id") is not None
        }
        resolved_policies = []
        for policy in policies:
            cached = (
                meta_by_slug.get(policy.get("slug"))
                or meta_by_id.get(str(policy.get("policy_id")))
                or {}
            )
            resolved_policies.append({
                **cached,
                **policy,
                "summary": policy.get("summary") or cached.get("summary"),
                "tag": policy.get("tag") or cached.get("tag"),
                "tagTone": policy.get("tagTone") or cached.get("tagTone"),
            })
    else:
        resolved_policies = meta_policies
    return ChatMessageItem(
        chat_message_id=str(message.chat_message_id),
        role=message.role,
        message_type=message.message_type,
        content=message.content,
        sequence_no=message.sequence_no,
        created_at=message.created_at,
        policies=[AssistantMessagePolicy(**p) for p in resolved_policies],
        evidences=[AssistantMessageEvidence(**e) for e in evidences],
        **meta,
    )


def _unwrap_message_meta(structured_json: dict | None) -> dict:
    if not structured_json:
        return {}
    apply_card_payload = structured_json.get("apply_card")
    apply_card = ApplyCard(**apply_card_payload) if apply_card_payload else None
    return {
        "user_status": structured_json.get("user_status"),
        "easy_summary": structured_json.get("easy_summary"),
        "key_points": structured_json.get("key_points", []),
        "sources": structured_json.get("sources", []),
        "policies": structured_json.get("policies", []),
        "actions": structured_json.get("actions", []),
        "apply_card": apply_card,
        "disclaimer": structured_json.get("disclaimer"),
        "slot_request": structured_json.get("slot_request"),
        "profile_confirm": structured_json.get("profile_confirm"),
    }


def _fallback_payload() -> dict:
    return {
        "content": "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
        "user_status": None,
        "easy_summary": None,
        "key_points": [],
        "sources": [],
        "policies": [],
        "evidences": [],
        "actions": [],
        "apply_card": None,
        "disclaimer": False,
        "slot_request": None,
        "profile_confirm": None,
    }

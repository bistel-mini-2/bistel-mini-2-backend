import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.chat_supervisor_graph import (
    HISTORY_LIMIT,
    chat_supervisor_graph,
)
from app.ai.nodes.chat.chat_nodes import BRANCH_LLM_TAG
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
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
    ChatSessionTitleUpdateResponse,
)
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
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)

        history = await _load_history(db, session.chat_session_id)
        is_first_message = not history and not session.title
        user_message = await _save_user_message(db, session.chat_session_id, content)
        await db.commit()

        graph_result = await _run_supervisor_graph(
            user_id=user_id,
            user_content=content,
            history=history,
            slot=session.slot_json or {},
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
        history = await _load_history(db, session.chat_session_id)
        is_first_message = not history and not session.title

        graph_state = {
            "user_id": session.user_id,
            "user_content": content,
            "history": history,
            "slot": session.slot_json or {},
        }

        final_state: dict[str, Any] = {}
        stream_failed = False
        try:
            async for event in chat_supervisor_graph.astream_events(
                graph_state, version="v2"
            ):
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

        try:
            user_message = await _save_user_message(
                db, session.chat_session_id, content
            )
            _, assistant_response = await _persist_assistant_outputs(
                db,
                session_id=session.chat_session_id,
                user_message_id=user_message.chat_message_id,
                graph_result=final_state,
                current_slot=session.slot_json or {},
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


# --- helpers ---------------------------------------------------------------


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


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
    *, user_id: int, user_content: str, history: list[dict], slot: dict | None = None
) -> dict:
    graph_state = {
        "user_id": user_id,
        "user_content": user_content,
        "history": history,
        "slot": slot or {},
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

    if not new_entries:
        return None

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

    return {
        "recent_policies": merged[:_SLOT_MAX_POLICIES],
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
        "sources": payload.get("sources", []),
        "actions": payload.get("actions", []),
        "apply_card": payload.get("apply_card"),
        "disclaimer": payload.get("disclaimer"),
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
        sources=payload.get("sources", []),
        policies=policies,
        actions=payload.get("actions", []),
        evidences=evidences,
        apply_card=apply_card,
        disclaimer=payload.get("disclaimer"),
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
    return ChatMessageItem(
        chat_message_id=str(message.chat_message_id),
        role=message.role,
        message_type=message.message_type,
        content=message.content,
        sequence_no=message.sequence_no,
        created_at=message.created_at,
        policies=[AssistantMessagePolicy(**p) for p in policies],
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
        "sources": structured_json.get("sources", []),
        "actions": structured_json.get("actions", []),
        "apply_card": apply_card,
        "disclaimer": structured_json.get("disclaimer"),
    }


def _fallback_payload() -> dict:
    return {
        "content": "죄송합니다. 답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
        "user_status": None,
        "sources": [],
        "policies": [],
        "evidences": [],
        "actions": [],
        "apply_card": None,
        "disclaimer": False,
    }

import asyncio
import logging
from datetime import datetime

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.graphs.chat_supervisor_graph import (
    HISTORY_LIMIT,
    chat_supervisor_graph,
)
from app.common.exceptions import AppException, ErrorCode
from app.db.models.chat_message import ChatMessage
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.repositories.policy_repository import PolicyRepository
from app.schemas.chat_schema import (
    AssistantMessage,
    AssistantMessageEvidence,
    AssistantMessagePolicy,
    ChatMessageItem,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
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
        )

        assistant_message, assistant_response = await _persist_assistant_outputs(
            db,
            session_id=session.chat_session_id,
            user_message_id=user_message.chat_message_id,
            graph_result=graph_result,
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


# --- helpers ---------------------------------------------------------------


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
    *, user_id: int, user_content: str, history: list[dict]
) -> dict:
    graph_state = {
        "user_id": user_id,
        "user_content": user_content,
        "history": history,
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

    response = _build_assistant_response(
        assistant_message,
        payload,
        policy_links_to_save,
        slug_to_policy_id,
    )
    return assistant_message, response


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
    return AssistantMessage(
        chat_message_id=str(assistant_message.chat_message_id),
        content=payload.get("content") or "",
        user_status=payload.get("user_status"),
        sources=payload.get("sources", []),
        policies=policies,
        actions=payload.get("actions", []),
        evidences=evidences,
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
    return {
        "user_status": structured_json.get("user_status"),
        "sources": structured_json.get("sources", []),
        "actions": structured_json.get("actions", []),
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
        "disclaimer": False,
    }

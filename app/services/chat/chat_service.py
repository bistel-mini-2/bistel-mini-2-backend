import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.result_adapters import _adapt_eligibility_result
from app.common.exceptions import AppException, ErrorCode
from app.db.models.eligibility_request import EligibilityRequest
from app.db.models.chat_session import ChatSession
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat_schema import (
    ChatSessionBulkDeleteResponse,
    ChatMessageListResponse,
    ChatMessageSendResponse,
    ChatSessionCreateResponse,
    ChatSessionDeleteResponse,
    ChatSessionListItem,
    ChatSessionListResponse,
    ChatSessionTitleUpdateResponse,
)
from app.services import chat_cancel_registry
from app.services.chat.ai._follow_up import (
    attach_similar_policies as _attach_similar_policies,
    build_eligibility_clarification_text as _build_eligibility_clarification_text,
    classify_follow_up_intent as _classify_follow_up_intent,
    find_follow_up_policy as _find_follow_up_policy,
    map_follow_up_answers as _map_follow_up_answers,
    run_follow_up_eligibility as _run_follow_up_eligibility,
)
from app.services.chat.persistence._message_serializer import (
    build_message_items as _build_message_items,
)
from app.services.chat.persistence._slot_builder import (
    build_next_slot as _build_next_slot,
)
from app.services.chat.persistence._persistence import (
    build_policy_link_rows as _build_policy_link_rows,
    build_evidence_rows as _build_evidence_rows,
    build_structured_json as _build_structured_json,
    fallback_payload as _fallback_payload,
    persist_assistant_outputs as _persist_assistant_outputs,
    resolve_policy_ids as _resolve_policy_ids,
)
from app.services.chat._routing import run_chat as _run_chat
from app.services.chat.persistence._session_helpers import (
    chat_session_id_from_source_ref as _chat_session_id_from_source_ref,
    get_owned_session_or_raise as _get_owned_session_or_raise,
    is_cancelled_or_deleted as _is_cancelled_or_deleted,
    load_history as _load_history,
    save_user_message as _save_user_message,
    schedule_title_generation as _schedule_title_generation,
)

logger = logging.getLogger(f"{__name__}.ChatService")

_INTENT_TO_SSE: dict[str, str] = defaultdict(lambda: "general", {"recommend": "recommendation"})  # type: ignore[assignment]


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _cancelled_event_payload() -> dict[str, str]:
    return {
        "type": "error",
        "code": ErrorCode.NOT_FOUND.value,
        "message": "삭제된 채팅 세션입니다.",
    }


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
        db: AsyncSession, user_id: int, limit: int | None = None
    ) -> ChatSessionListResponse:
        sessions = await ChatRepository.find_sessions_by_user(db, user_id, limit)
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

            graph_result = await _run_chat(
                db=db,
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
                db, session.chat_session_id, datetime.now(timezone.utc)
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
    async def persist_eligibility_result_message(
        db: AsyncSession,
        *,
        user_id: int,
        request: EligibilityRequest,
        result_json: dict[str, Any],
    ) -> None:
        chat_session_id = _chat_session_id_from_source_ref(request.source_ref_id)
        if chat_session_id is None:
            return
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        if await ChatRepository.eligibility_result_message_exists(
            db,
            chat_session_id=chat_session_id,
            request_id=request.request_id,
        ):
            return

        content_text, user_status, policies, evidences = _adapt_eligibility_result(
            result_json,
            fallback_slug=str(result_json.get("slug") or request.policy_id),
            fallback_policy_name=result_json.get("policy_name"),
        )
        policy_slug = str(result_json.get("slug") or request.policy_id)
        policy_links = [{"policy_slug": policy_slug, "action_type": "ELIGIBILITY_TARGET"}]
        payload = {
            "content": content_text,
            "user_status": user_status,
            "policies": policies,
            "evidences": evidences,
            "actions": ["eligibility"],
            "disclaimer": True,
            "eligibility_result": result_json,
        }
        sequence_no = await ChatRepository.next_sequence_no(db, chat_session_id)
        from app.db.models.chat_message import ChatMessage
        structured_json = _build_structured_json(
            {"intent": "eligibility", "raw": "eligibility_request"},
            payload,
        )
        assistant_message = await ChatRepository.save_message(
            db,
            ChatMessage(
                chat_session_id=chat_session_id,
                parent_message_id=None,
                role="assistant",
                message_type="TEXT",
                content=content_text,
                structured_json=structured_json,
                sequence_no=sequence_no,
            ),
        )
        slug_to_policy_id = await _resolve_policy_ids(db, policy_links)
        await ChatRepository.bulk_save_message_policies(
            db,
            _build_policy_link_rows(
                assistant_message.chat_message_id,
                policy_links,
                slug_to_policy_id,
            ),
        )
        await ChatRepository.bulk_save_message_evidences(
            db,
            _build_evidence_rows(
                assistant_message.chat_message_id,
                [
                    {
                        "chunk_id": ev["chunk_id"],
                        "snippet": ev.get("snippet"),
                        "evidence_role": ev.get("evidence_role"),
                    }
                    for ev in evidences
                    if ev.get("chunk_id") is not None
                ],
            ),
        )
        if result_json.get("status") == "FOLLOW_UP_REQUIRED":
            slot_policy_ids = dict(slug_to_policy_id)
            if policy_slug not in slot_policy_ids:
                slot_policy_ids[policy_slug] = int(request.policy_id)
            next_slot = _build_next_slot(
                current_slot=session.slot_json or {},
                policy_links=policy_links,
                branch_policies=policies,
                slug_to_policy_id=slot_policy_ids,
                eligibility_slot_update={
                    "slug": policy_slug,
                    "eligibility_request_id": request.request_id,
                    "follow_up_questions": (
                        result_json.get("follow_up_questions")
                        or result_json.get("questions")
                        or []
                    ),
                    "eligibility_status": result_json.get("status"),
                },
            )
            if next_slot is not None:
                await ChatRepository.update_session_slot(
                    db,
                    chat_session_id,
                    next_slot,
                )
        await ChatRepository.update_last_message_at(
            db,
            chat_session_id,
            datetime.now(timezone.utc),
        )

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

            follow_up_policy = _find_follow_up_policy(slot)
            if follow_up_policy:
                follow_up_intent = await _classify_follow_up_intent(
                    follow_up_policy, history, content
                )
                if follow_up_intent != "other_intent":
                    yield _sse_event({"type": "intent", "intent": follow_up_intent})

                if follow_up_intent == "general":
                    manual_confirmations = await _map_follow_up_answers(follow_up_policy, content)
                    result_json = await _run_follow_up_eligibility(
                        db, session.user_id, content, follow_up_policy, manual_confirmations
                    )
                    if result_json is None:
                        yield _sse_event({
                            "type": "error",
                            "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                            "message": "답변을 분석하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        })
                        return
                    policy_slug = follow_up_policy["slug"]
                    policy_name = follow_up_policy.get("policy_name")
                    content_text, user_status, e_policies, e_evidences = _adapt_eligibility_result(
                        result_json,
                        fallback_slug=policy_slug,
                        fallback_policy_name=policy_name,
                    )
                    result_status = result_json.get("status")
                    follow_up_graph_result: dict[str, Any] = {
                        "assistant_payload": {
                            "content": content_text,
                            "user_status": user_status,
                            "policies": e_policies,
                            "evidences": e_evidences,
                            "actions": ["eligibility"],
                            "disclaimer": True,
                            "eligibility_result": {
                                "status": result_status,
                                "user_status": result_json.get("user_status"),
                                "assessment_status": result_json.get("assessment_status"),
                                "follow_up_questions": result_json.get("follow_up_questions") or [],
                                "summary": result_json.get("summary"),
                                "request_id": result_json.get("request_id"),
                                "criteria": result_json.get("criteria") or result_json.get("criteria_results") or [],
                            },
                        },
                        "supervisor_decision": {"intent": "eligibility", "raw": "follow_up_answer"},
                        "evidences_to_save": [
                            {
                                "chunk_id": ev["chunk_id"],
                                "snippet": ev.get("snippet"),
                                "evidence_role": ev.get("evidence_role"),
                            }
                            for ev in e_evidences
                            if ev.get("chunk_id") is not None
                        ],
                        "policy_links_to_save": [
                            {"policy_slug": policy_slug, "action_type": "ELIGIBILITY"}
                        ],
                        "eligibility_slot_update": {
                            "slug": policy_slug,
                            "eligibility_request_id": result_json.get("request_id"),
                            "follow_up_questions": result_json.get("follow_up_questions") or [],
                            "eligibility_status": result_status,
                        },
                    }
                    yield _sse_event({"type": "token", "delta": content_text})
                    try:
                        user_message = await _save_user_message(
                            db, session.chat_session_id, content
                        )
                        _, assistant_response = await _persist_assistant_outputs(
                            db,
                            session_id=session.chat_session_id,
                            user_message_id=user_message.chat_message_id,
                            graph_result=follow_up_graph_result,
                            current_slot=slot,
                        )
                        await ChatRepository.update_last_message_at(
                            db, session.chat_session_id, datetime.now(timezone.utc)
                        )
                        await db.commit()
                    except Exception:
                        await db.rollback()
                        logger.exception("Persisting follow_up eligibility answer failed")
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
                    yield _sse_event({"type": "done", "payload": response.model_dump(mode="json")})
                    return

                elif follow_up_intent == "eligibility_clarification":
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
                            db, session.chat_session_id, datetime.now(timezone.utc)
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

            _is_follow_up_recommendation = (
                follow_up_policy is not None
                and follow_up_intent == "recommendation"  # type: ignore[possibly-undefined]
            )
            preseed_result: dict[str, Any] = (
                {
                    "eligibility_slot_update": {
                        "slug": follow_up_policy["slug"],
                        "eligibility_request_id": follow_up_policy.get("eligibility_request_id"),
                        "follow_up_questions": [],
                        "eligibility_status": None,
                    }
                }
                if _is_follow_up_recommendation
                else {}
            )
            stream_failed = False
            final_state: dict[str, Any] = {}
            stream_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

            async def _queue_intent(intent: str) -> None:
                await stream_queue.put({"type": "intent", "intent": intent})

            async def _queue_token(delta: str) -> None:
                await stream_queue.put({"type": "token", "delta": delta})

            intent_already_emitted = (
                follow_up_policy is not None
                and follow_up_intent != "other_intent"  # type: ignore[possibly-undefined]
            )

            async def _run_stream_chat() -> dict[str, Any]:
                try:
                    return await _run_chat(
                        db=db,
                        user_id=session.user_id,
                        user_content=content,
                        history=history,
                        slot=slot,
                        recent_assistant_policy=recent_assistant_policy,
                        emit_intent=not intent_already_emitted,
                        on_intent=_queue_intent,
                        on_token=_queue_token,
                        preseed_result=preseed_result,
                    )
                finally:
                    await stream_queue.put(None)

            stream_task = asyncio.create_task(_run_stream_chat())
            try:
                while True:
                    if cancel_event.is_set():
                        stream_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await stream_task
                        await db.rollback()
                        yield _sse_event(_cancelled_event_payload())
                        return

                    try:
                        queued_event = await asyncio.wait_for(stream_queue.get(), 0.2)
                    except asyncio.TimeoutError:
                        continue

                    if queued_event is None:
                        break
                    yield _sse_event(queued_event)

                final_state = await stream_task
            except Exception:
                stream_failed = True
                logger.exception("Chat direct routing streaming failed")

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
                    db, session.chat_session_id, datetime.now(timezone.utc)
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

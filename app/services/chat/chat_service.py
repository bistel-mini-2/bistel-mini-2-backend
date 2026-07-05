import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from fastapi import status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.nodes.chat.result_adapters import _adapt_eligibility_result
from app.common.exceptions import AppException, ErrorCode
from app.db.models.eligibility_request import EligibilityRequest
from app.db.models.chat_request import ChatRequest
from app.db.models.chat_session import ChatSession
from app.db.session import AsyncSessionLocal
from app.repositories.chat_request_repository import ChatRequestRepository
from app.repositories.chat_repository import ChatRepository
from app.schemas.chat_schema import (
    ChatRequestStatusResponse,
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
from app.services.chat.ai import _follow_up as _follow_up_module
from app.services.chat.persistence._message_serializer import (
    build_message_items as _build_message_items,
    to_message_item as _to_message_item,
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
_FOLLOW_UP_LLM = _follow_up_module._FOLLOW_UP_LLM

_attach_similar_policies = _follow_up_module.attach_similar_policies
_build_eligibility_clarification_text = (
    _follow_up_module.build_eligibility_clarification_text
)
_find_follow_up_policy = _follow_up_module.find_follow_up_policy
_run_follow_up_eligibility = _follow_up_module.run_follow_up_eligibility


async def _classify_follow_up_intent(*args: Any, **kwargs: Any) -> str:
    _follow_up_module._FOLLOW_UP_LLM = _FOLLOW_UP_LLM
    return await _follow_up_module.classify_follow_up_intent(*args, **kwargs)


async def _map_follow_up_answers(*args: Any, **kwargs: Any) -> list[dict]:
    _follow_up_module._FOLLOW_UP_LLM = _FOLLOW_UP_LLM
    return await _follow_up_module.map_follow_up_answers(*args, **kwargs)


def _sse_event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _retryable_chat_request(status_value: str, error_code: str | None) -> bool:
    return status_value == "failed" and error_code != ErrorCode.INVALID_INPUT.value


def _chat_request_response(request: ChatRequest) -> ChatRequestStatusResponse:
    return ChatRequestStatusResponse(
        request_id=str(request.request_id),
        chat_session_id=str(request.chat_session_id),
        user_message_id=str(request.user_message_id),
        idempotency_key=request.idempotency_key,
        status=request.status,
        intent=request.intent,
        error_code=request.error_code,
        error_message=request.error_message,
        assistant_message_id=(
            str(request.assistant_message_id)
            if request.assistant_message_id is not None
            else None
        ),
        retryable=_retryable_chat_request(request.status, request.error_code),
        payload=request.response_payload_json,
        created_at=request.created_at,
        completed_at=request.completed_at,
        updated_at=request.updated_at,
    )


def _intent_from_graph_result(graph_result: dict[str, Any]) -> str | None:
    decision = graph_result.get("supervisor_decision") or {}
    intent = decision.get("intent")
    return str(intent) if intent else None


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
        idempotency_key: str | None = None,
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
    async def get_request_status(
        db: AsyncSession,
        *,
        user_id: int,
        request_id: int,
    ) -> ChatRequestStatusResponse:
        request = await ChatRequestRepository.find_by_id(db, request_id)
        if request is None:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Chat request not found",
            )
        session = await ChatRepository.find_session_by_id(db, request.chat_session_id)
        if session is None or session.user_id != user_id:
            raise AppException(
                status_code=status.HTTP_404_NOT_FOUND,
                code=ErrorCode.NOT_FOUND,
                message="Chat request not found",
            )
        return _chat_request_response(request)

    @staticmethod
    async def get_latest_incomplete_request(
        db: AsyncSession,
        *,
        user_id: int,
        chat_session_id: int,
    ) -> ChatRequestStatusResponse | None:
        session = await _get_owned_session_or_raise(db, user_id, chat_session_id)
        request = await ChatRequestRepository.find_latest_incomplete(
            db, session.chat_session_id
        )
        return _chat_request_response(request) if request else None

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
        idempotency_key: str | None = None,
    ) -> AsyncIterator[str]:
        cancel_event = chat_cancel_registry.register(session.chat_session_id)
        chat_request: ChatRequest | None = None
        stream_task: asyncio.Task[dict[str, Any]] | None = None
        history: list[dict[str, Any]] = []
        slot: dict[str, Any] = session.slot_json or {}
        recent_assistant_policy: dict[str, Any] | None = None

        async def _run_disconnect_recovery(
            *,
            request_id: int,
            user_message_id: int,
            history_snapshot: list[dict[str, Any]],
            slot_snapshot: dict[str, Any],
            recent_policy_snapshot: dict[str, Any] | None,
        ) -> None:
            async with AsyncSessionLocal() as task_db:
                try:
                    final_state = await _run_chat(
                        db=task_db,
                        user_id=session.user_id,
                        user_content=content,
                        history=history_snapshot,
                        slot=slot_snapshot,
                        recent_assistant_policy=recent_policy_snapshot,
                    )
                    request = await ChatRequestRepository.find_by_id(task_db, request_id)
                    if request is None or request.status != "processing":
                        await task_db.rollback()
                        return
                    locked_session = await ChatRequestRepository.lock_session_for_slot_update(
                        task_db,
                        session.chat_session_id,
                    )
                    if locked_session is None:
                        await ChatRequestRepository.mark_failed(
                            task_db,
                            request,
                            error_code=ErrorCode.NOT_FOUND.value,
                            error_message="Chat session not found",
                        )
                        await task_db.commit()
                        return
                    assistant_message, assistant_response = await _persist_assistant_outputs(
                        task_db,
                        session_id=session.chat_session_id,
                        user_message_id=user_message_id,
                        graph_result=final_state,
                        current_slot=locked_session.slot_json or {},
                    )
                    await ChatRepository.update_last_message_at(
                        task_db,
                        session.chat_session_id,
                        datetime.now(timezone.utc),
                    )
                    response = ChatMessageSendResponse(
                        chat_session_id=str(session.chat_session_id),
                        user_message_id=str(user_message_id),
                        assistant_message=assistant_response,
                    )
                    await ChatRequestRepository.mark_completed(
                        task_db,
                        request,
                        intent=_intent_from_graph_result(final_state),
                        assistant_message_id=assistant_message.chat_message_id,
                        response_payload_json=response.model_dump(mode="json"),
                    )
                    await task_db.commit()
                except Exception:
                    await task_db.rollback()
                    logger.exception(
                        "Chat disconnect recovery failed: request_id=%s",
                        request_id,
                    )
                    try:
                        request = await ChatRequestRepository.find_by_id(task_db, request_id)
                        if request is not None and request.status == "processing":
                            await ChatRequestRepository.mark_failed(
                                task_db,
                                request,
                                error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                                error_message="답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                            )
                            await task_db.commit()
                    except Exception:
                        await task_db.rollback()
                        logger.exception(
                            "Failed to mark disconnect recovery failed: request_id=%s",
                            request_id,
                        )

        async def _yield_existing_request(request: ChatRequest) -> AsyncIterator[str]:
            if request.status == "completed" and request.response_payload_json:
                yield _sse_event({
                    "type": "done",
                    "request_id": str(request.request_id),
                    "payload": request.response_payload_json,
                })
                return
            if request.status == "processing":
                yield _sse_event({
                    "type": "accepted",
                    "request_id": str(request.request_id),
                    "status": request.status,
                })
                return
            if request.status == "cancelled":
                yield _sse_event({
                    "type": "cancelled",
                    "request_id": str(request.request_id),
                    "status": request.status,
                })
                return
            yield _sse_event({
                "type": "error",
                "request_id": str(request.request_id),
                "code": request.error_code or ErrorCode.INTERNAL_SERVER_ERROR.value,
                "message": request.error_message or "답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
            })

        async def _mark_failed(
            request_id: int,
            *,
            error_code: str,
            error_message: str,
        ) -> None:
            await db.rollback()
            try:
                request = await ChatRequestRepository.find_by_id(db, request_id)
                if request is not None and request.status == "processing":
                    await ChatRequestRepository.mark_failed(
                        db,
                        request,
                        error_code=error_code,
                        error_message=error_message,
                    )
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("Failed to persist chat_request failure: %s", request_id)

        async def _mark_cancelled(request_id: int) -> None:
            await db.rollback()
            try:
                request = await ChatRequestRepository.find_by_id(db, request_id)
                if request is not None and request.status == "processing":
                    await ChatRequestRepository.mark_cancelled(db, request)
                await db.commit()
            except Exception:
                await db.rollback()
                logger.exception("Failed to persist chat_request cancellation: %s", request_id)

        async def _persist_success(final_state: dict[str, Any]) -> ChatMessageSendResponse:
            if chat_request is None:
                raise RuntimeError("chat_request is not initialized")
            locked_session = await ChatRequestRepository.lock_session_for_slot_update(
                db,
                session.chat_session_id,
            )
            if locked_session is None:
                raise AppException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    code=ErrorCode.NOT_FOUND,
                    message="Chat session not found",
                )
            assistant_message, assistant_response = await _persist_assistant_outputs(
                db,
                session_id=session.chat_session_id,
                user_message_id=chat_request.user_message_id,
                graph_result=final_state,
                current_slot=locked_session.slot_json or {},
            )
            await ChatRepository.update_last_message_at(
                db, session.chat_session_id, datetime.now(timezone.utc)
            )
            response = ChatMessageSendResponse(
                chat_session_id=str(session.chat_session_id),
                user_message_id=str(chat_request.user_message_id),
                assistant_message=assistant_response,
            )
            await ChatRequestRepository.mark_completed(
                db,
                chat_request,
                intent=_intent_from_graph_result(final_state),
                assistant_message_id=assistant_message.chat_message_id,
                response_payload_json=response.model_dump(mode="json"),
            )
            await db.commit()
            return response

        try:
            history = await _load_history(db, session.chat_session_id)
            is_first_message = not history and not session.title
            recent_assistant_policy = await ChatRepository.find_recent_assistant_policy(
                db, session.chat_session_id
            )
            slot = session.slot_json or {}
            if idempotency_key:
                existing_request = await ChatRequestRepository.find_by_idempotency_key(
                    db,
                    chat_session_id=session.chat_session_id,
                    idempotency_key=idempotency_key,
                )
                if existing_request is not None:
                    async for event in _yield_existing_request(existing_request):
                        yield event
                    return

            try:
                user_message = await _save_user_message(
                    db, session.chat_session_id, content
                )
                chat_request = await ChatRequestRepository.create_processing(
                    db,
                    chat_session_id=session.chat_session_id,
                    user_message_id=user_message.chat_message_id,
                    idempotency_key=idempotency_key,
                )
                await db.commit()
            except IntegrityError:
                await db.rollback()
                if not idempotency_key:
                    raise
                existing_request = await ChatRequestRepository.find_by_idempotency_key(
                    db,
                    chat_session_id=session.chat_session_id,
                    idempotency_key=idempotency_key,
                )
                if existing_request is None:
                    raise
                async for event in _yield_existing_request(existing_request):
                    yield event
                return
            yield _sse_event({
                "type": "accepted",
                "request_id": str(chat_request.request_id),
                "status": chat_request.status,
            })

            follow_up_policy = _find_follow_up_policy(slot)
            if follow_up_policy:
                follow_up_intent = await _classify_follow_up_intent(
                    follow_up_policy, history, content
                )
                if follow_up_intent != "other_intent":
                    yield _sse_event({
                        "type": "intent",
                        "intent": follow_up_intent,
                        "request_id": str(chat_request.request_id),
                    })

                if follow_up_intent == "general":
                    manual_confirmations = await _map_follow_up_answers(follow_up_policy, content)
                    result_json = await _run_follow_up_eligibility(
                        db, session.user_id, content, follow_up_policy, manual_confirmations
                    )
                    if result_json is None:
                        await _mark_failed(
                            chat_request.request_id,
                            error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                            error_message="답변을 분석하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        )
                        yield _sse_event({
                            "type": "error",
                            "request_id": str(chat_request.request_id),
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
                    yield _sse_event({
                        "type": "token",
                        "delta": content_text,
                        "request_id": str(chat_request.request_id),
                    })
                    try:
                        response = await _persist_success(follow_up_graph_result)
                    except Exception:
                        await _mark_failed(
                            chat_request.request_id,
                            error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                            error_message="답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        )
                        logger.exception("Persisting follow_up eligibility answer failed")
                        yield _sse_event({
                            "type": "error",
                            "request_id": str(chat_request.request_id),
                            "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                            "message": "답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        })
                        return
                    if is_first_message:
                        _schedule_title_generation(session.chat_session_id, content)
                    yield _sse_event({
                        "type": "done",
                        "request_id": str(chat_request.request_id),
                        "payload": response.model_dump(mode="json"),
                    })
                    return

                elif follow_up_intent == "eligibility_clarification":
                    clarification_text = _build_eligibility_clarification_text(
                        follow_up_policy.get("follow_up_questions") or []
                    )
                    yield _sse_event({
                        "type": "token",
                        "delta": clarification_text,
                        "request_id": str(chat_request.request_id),
                    })
                    try:
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
                        response = await _persist_success(minimal_result)
                    except Exception:
                        await _mark_failed(
                            chat_request.request_id,
                            error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                            error_message="답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        )
                        logger.exception("Persisting eligibility_clarification message failed")
                        yield _sse_event({
                            "type": "error",
                            "request_id": str(chat_request.request_id),
                            "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                            "message": "답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        })
                        return
                    yield _sse_event({
                        "type": "done",
                        "request_id": str(chat_request.request_id),
                        "payload": response.model_dump(mode="json"),
                    })
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
                await stream_queue.put({
                    "type": "intent",
                    "intent": intent,
                    "request_id": str(chat_request.request_id),
                })

            async def _queue_token(delta: str) -> None:
                await stream_queue.put({
                    "type": "token",
                    "delta": delta,
                    "request_id": str(chat_request.request_id),
                })

            async def _queue_progress(
                flow: str, node: str, status: str, step: int, total: int
            ) -> None:
                from app.ai.utils.progress import get_node_label
                await stream_queue.put({
                    "type": "progress",
                    "request_id": str(chat_request.request_id),
                    "flow": flow,
                    "node": node,
                    "status": status,
                    "step": step,
                    "total_steps": total,
                    "label": get_node_label(flow, node, status),
                })

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
                        on_progress=_queue_progress,
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
                        await _mark_cancelled(chat_request.request_id)
                        yield _sse_event({
                            "type": "cancelled",
                            "request_id": str(chat_request.request_id),
                            "status": "cancelled",
                        })
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
            except asyncio.CancelledError:
                # Client disconnect is not an explicit user cancellation. Continue
                # the in-flight handler and persist the final request state.
                try:
                    final_state = await stream_task
                    if final_state.get("assistant_payload"):
                        await _persist_success(final_state)
                    else:
                        await _mark_failed(
                            chat_request.request_id,
                            error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                            error_message="답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                        )
                except Exception:
                    await _mark_failed(
                        chat_request.request_id,
                        error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                        error_message="답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                    )
                    logger.exception("Chat stream disconnected and background persist failed")
                return

            if stream_failed or not final_state.get("assistant_payload"):
                await _mark_failed(
                    chat_request.request_id,
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                    error_message="답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                )
                yield _sse_event({
                    "type": "error",
                    "request_id": str(chat_request.request_id),
                    "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                    "message": "답변을 생성하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                })
                return

            if await _is_cancelled_or_deleted(
                db, session.chat_session_id, cancel_event
            ):
                await _mark_cancelled(chat_request.request_id)
                yield _sse_event({
                    "type": "cancelled",
                    "request_id": str(chat_request.request_id),
                    "status": "cancelled",
                })
                return

            try:
                response = await _persist_success(final_state)
            except Exception:
                await _mark_failed(
                    chat_request.request_id,
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR.value,
                    error_message="답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                )
                logger.exception("Persisting chat messages failed during stream")
                yield _sse_event({
                    "type": "error",
                    "request_id": str(chat_request.request_id),
                    "code": ErrorCode.INTERNAL_SERVER_ERROR.value,
                    "message": "답변을 저장하는 중 문제가 발생했어요. 잠시 후 다시 시도해 주세요.",
                })
                return

            if is_first_message:
                _schedule_title_generation(session.chat_session_id, content)

            yield _sse_event({
                "type": "done",
                "request_id": str(chat_request.request_id),
                "payload": response.model_dump(mode="json"),
            })
        except asyncio.CancelledError:
            if chat_request is not None:
                asyncio.create_task(
                    _run_disconnect_recovery(
                        request_id=chat_request.request_id,
                        user_message_id=chat_request.user_message_id,
                        history_snapshot=list(history),
                        slot_snapshot=dict(slot),
                        recent_policy_snapshot=(
                            dict(recent_assistant_policy)
                            if recent_assistant_policy is not None
                            else None
                        ),
                    )
                )
                return
            raise
        finally:
            chat_cancel_registry.unregister(session.chat_session_id, cancel_event)

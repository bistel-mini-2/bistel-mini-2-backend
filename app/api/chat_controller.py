from fastapi import APIRouter, Query, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.chat_schema import (
    ChatMessageSendRequest,
    ChatSessionBulkDeleteRequest,
    ChatSessionCreateRequest,
    ChatSessionTitleUpdateRequest,
)
from app.services.chat_service import ChatService


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


router = APIRouter(prefix="/api/v1/chat", tags=["Chat"])


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
async def create_chat_session(
    payload: ChatSessionCreateRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.create_session(
        db, user_id=current_user.user_id, title=payload.title
    )
    return success_response(data=response, status_code=status.HTTP_201_CREATED)


@router.get("/sessions")
async def list_chat_sessions(
    db: DbSessionDep,
    current_user: CurrentUserDep,
    limit: int | None = Query(default=None, ge=1, le=100),
) -> JSONResponse:
    # limit 미지정(채팅 화면)은 전체, 지정(마이페이지 limit=20)은 상한 적용.
    response = await ChatService.list_sessions(
        db, user_id=current_user.user_id, limit=limit
    )
    return success_response(data=response)


@router.post("/sessions/bulk-delete")
async def bulk_delete_chat_sessions(
    payload: ChatSessionBulkDeleteRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.bulk_delete_sessions(
        db,
        user_id=current_user.user_id,
        chat_session_ids=payload.chat_session_ids,
    )
    return success_response(data=response)


@router.patch("/sessions/{chat_session_id}")
async def update_chat_session_title(
    chat_session_id: int,
    payload: ChatSessionTitleUpdateRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.update_session_title(
        db,
        user_id=current_user.user_id,
        chat_session_id=chat_session_id,
        title=payload.title,
    )
    return success_response(data=response)


@router.delete("/sessions/{chat_session_id}")
async def delete_chat_session(
    chat_session_id: int,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.delete_session(
        db,
        user_id=current_user.user_id,
        chat_session_id=chat_session_id,
    )
    return success_response(data=response)


@router.get("/sessions/{chat_session_id}/messages")
async def list_chat_messages(
    chat_session_id: int,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.list_messages(
        db, user_id=current_user.user_id, chat_session_id=chat_session_id
    )
    return success_response(data=response)


@router.post(
    "/sessions/{chat_session_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
async def send_chat_message(
    chat_session_id: int,
    payload: ChatMessageSendRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> JSONResponse:
    response = await ChatService.send_message(
        db,
        user_id=current_user.user_id,
        chat_session_id=chat_session_id,
        content=payload.content,
    )
    return success_response(data=response, status_code=status.HTTP_201_CREATED)


@router.post("/sessions/{chat_session_id}/messages/stream")
async def stream_chat_message(
    chat_session_id: int,
    payload: ChatMessageSendRequest,
    db: DbSessionDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    session = await ChatService.ensure_owned_session(
        db, user_id=current_user.user_id, chat_session_id=chat_session_id
    )
    return StreamingResponse(
        ChatService.send_message_stream(db, session=session, content=payload.content),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

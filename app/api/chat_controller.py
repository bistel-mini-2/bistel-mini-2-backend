from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from app.common.response import success_response
from app.core.dependencies import CurrentUserDep, DbSessionDep
from app.schemas.chat_schema import ChatMessageSendRequest, ChatSessionCreateRequest
from app.services.chat_service import ChatService


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
) -> JSONResponse:
    response = await ChatService.list_sessions(db, user_id=current_user.user_id)
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

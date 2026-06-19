from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=255)


class ChatSessionCreateResponse(BaseModel):
    chat_session_id: str
    session_status: str


class ChatSessionListItem(BaseModel):
    chat_session_id: str
    title: str | None
    session_status: str
    last_message_at: datetime | None
    created_at: datetime | None
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ChatSessionListResponse(BaseModel):
    sessions: list[ChatSessionListItem]


class ChatMessageItem(BaseModel):
    chat_message_id: str
    role: str
    message_type: str
    content: str | None
    sequence_no: int
    created_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ChatMessageListResponse(BaseModel):
    chat_session_id: str
    messages: list[ChatMessageItem]

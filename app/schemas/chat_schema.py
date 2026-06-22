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


class AssistantMessagePolicy(BaseModel):
    policy_id: str
    slug: str
    policy_name: str
    summary: str | None = None
    tag: str | None = None
    tagTone: str | None = None


class AssistantMessageEvidence(BaseModel):
    snippet: str
    source_title: str | None = None
    source_url: str | None = None
    evidence_role: str | None = None


class AssistantMessage(BaseModel):
    chat_message_id: str
    content: str
    user_status: str | None = None
    sources: list[str] = Field(default_factory=list)
    policies: list[AssistantMessagePolicy] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    evidences: list[AssistantMessageEvidence] = Field(default_factory=list)
    disclaimer: bool | None = None


class ChatMessageSendRequest(BaseModel):
    content: str = Field(..., min_length=1)


class ChatMessageSendResponse(BaseModel):
    chat_session_id: str
    user_message_id: str
    assistant_message: AssistantMessage


class ChatMessageItem(BaseModel):
    chat_message_id: str
    role: str
    message_type: str
    content: str | None
    sequence_no: int
    created_at: datetime | None
    user_status: str | None = None
    sources: list[str] = Field(default_factory=list)
    policies: list[AssistantMessagePolicy] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    evidences: list[AssistantMessageEvidence] = Field(default_factory=list)
    disclaimer: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class ChatMessageListResponse(BaseModel):
    chat_session_id: str
    messages: list[ChatMessageItem]

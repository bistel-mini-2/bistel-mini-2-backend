from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ChatRequest(Base):
    __tablename__ = "chat_request"
    __table_args__ = (
        UniqueConstraint(
            "chat_session_id",
            "idempotency_key",
            name="chat_request_session_idempotency_key_uk",
        ),
    )

    request_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_session.chat_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="processing",
        server_default="processing",
    )
    intent: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    assistant_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="SET NULL"),
        nullable=True,
    )
    response_payload_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

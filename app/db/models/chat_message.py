from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ChatMessage(Base):
    __tablename__ = "chat_message"

    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_session_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_session.chat_session_id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_message_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="SET NULL"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    message_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="TEXT",
        server_default="TEXT",
    )
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    structured_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        server_default=func.now(),
    )

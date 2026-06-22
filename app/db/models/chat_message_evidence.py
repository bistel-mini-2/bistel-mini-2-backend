from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ChatMessageEvidence(Base):
    __tablename__ = "chat_message_evidence"

    chat_message_evidence_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy_document_chunk.chunk_id", ondelete="CASCADE"),
        nullable=False,
    )
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_role: Mapped[str | None] = mapped_column(String(30), nullable=True)

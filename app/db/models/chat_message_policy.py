from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ChatMessagePolicy(Base):
    __tablename__ = "chat_message_policy"

    chat_message_policy_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    chat_message_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("chat_message.chat_message_id", ondelete="CASCADE"),
        nullable=False,
    )
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
    )
    action_type: Mapped[str] = mapped_column(String(30), nullable=False)

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserPolicyChecklistItem(Base):
    __tablename__ = "user_policy_checklist_item"

    user_checklist_item_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    progress_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_policy_progress.progress_id", ondelete="CASCADE"),
        nullable=False,
    )
    template_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy_checklist_template.template_item_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.now(), onupdate=func.now()
    )

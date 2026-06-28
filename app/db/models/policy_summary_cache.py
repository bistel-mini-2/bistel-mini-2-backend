from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PolicySummaryCache(Base):
    __tablename__ = "policy_summary_cache"

    summary_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    condition_profile_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "policy_condition_profile.condition_profile_id",
            ondelete="SET NULL",
        ),
        nullable=True,
        index=True,
    )
    condition_profile_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    summary_source: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="policy_condition_profile",
        server_default="policy_condition_profile",
    )
    request_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PROCESSING",
        server_default="PROCESSING",
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

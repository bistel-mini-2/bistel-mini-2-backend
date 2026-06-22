from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.schemas.ai_contract import RequestStatus


class EligibilityRequest(Base):
    __tablename__ = "eligibility_request"

    request_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="POLICY_DETAIL",
        server_default="POLICY_DETAIL",
    )
    source_ref_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_query_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    merged_condition_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    profile_conflict_json: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=RequestStatus.READY.value,
        server_default=RequestStatus.READY.value,
    )
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

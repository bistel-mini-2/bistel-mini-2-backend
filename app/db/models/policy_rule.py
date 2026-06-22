from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PolicyRule(Base):
    __tablename__ = "policy_rule"

    rule_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    operator: Mapped[str] = mapped_column(String(30), nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    is_hard_filter: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    manual_check_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    manual_check_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

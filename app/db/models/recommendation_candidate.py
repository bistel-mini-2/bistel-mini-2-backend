from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RecommendationCandidate(Base):
    __tablename__ = "recommendation_candidate"
    __table_args__ = (
        UniqueConstraint(
            "request_id",
            "policy_id",
            name="recommendation_candidate_request_policy_uidx",
        ),
    )

    candidate_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("recommendation_request.request_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filter_match_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    retrieval_score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    rerank_score: Mapped[float | None] = mapped_column(Numeric(10, 4), nullable=True)
    candidate_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="CANDIDATE",
        server_default="CANDIDATE",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

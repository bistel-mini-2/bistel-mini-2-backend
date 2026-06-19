from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PolicyAssessment(Base):
    __tablename__ = "policy_assessment"

    assessment_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    request_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    recommendation_request_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    eligibility_request_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        nullable=False,
    )
    assessment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    assessment_status: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    matched_conditions_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    missing_conditions_json: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    conflicting_conditions_json: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    manual_check_points_json: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_for_result: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )


class AssessmentEvidence(Base):
    __tablename__ = "assessment_evidence"

    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    assessment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy_assessment.assessment_id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy_document_chunk.chunk_id", ondelete="CASCADE"),
        nullable=False,
    )
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    similarity_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    evidence_role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )

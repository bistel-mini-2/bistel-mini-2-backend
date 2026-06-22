from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Policy(Base):
    __tablename__ = "policy"
    __table_args__ = (
        Index("ix_policy_policy_name", "policy_name"),
        Index("ix_policy_main_category", "main_category"),
        Index("ix_policy_region_code", "region_code"),
    )

    policy_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    policy_code: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    policy_name: Mapped[str] = mapped_column(String(255), nullable=False)
    main_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sub_category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    region_scope: Mapped[str | None] = mapped_column(String(50), nullable=True)
    region_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    benefit_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    application_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    application_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    application_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    official_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, server_default=func.now(), onupdate=func.now()
    )

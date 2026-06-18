from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class PolicyDetail(Base):
    __tablename__ = "policy_detail"

    policy_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("policy.policy_id", ondelete="CASCADE"),
        primary_key=True,
    )
    easy_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    benefit_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_method: Mapped[str | None] = mapped_column(Text, nullable=True)
    application_period_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    caution: Mapped[str | None] = mapped_column(Text, nullable=True)

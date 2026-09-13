from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
    ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class User(Timestamps, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    password_hash: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    is_admin: Mapped[bool] = mapped_column(Boolean, server_default="false")

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)


class CloudProvider(Timestamps, Base):
    __tablename__ = "cloud_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    website_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)


class SustainabilityMetric(Timestamps, Base):
    __tablename__ = "sustainability_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("cloud_providers.id", ondelete="CASCADE"), index=True)
    metric_name: Mapped[str] = mapped_column(String(120))
    metric_value: Mapped[str] = mapped_column(Text)
    unit: Mapped[str | None] = mapped_column(String(80))
    source_url: Mapped[str] = mapped_column(Text)
    source_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[CloudProvider] = relationship()


class UploadedBill(Timestamps, Base):
    __tablename__ = "uploaded_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_providers.id", ondelete="SET NULL"), index=True)
    original_filename: Mapped[str] = mapped_column(Text)
    storage_key: Mapped[str] = mapped_column(Text, unique=True)
    file_type: Mapped[str] = mapped_column(String(80))
    file_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), server_default="uploaded")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()
    provider: Mapped[CloudProvider | None] = relationship()

    __table_args__ = (
        CheckConstraint("file_size > 0", name="ck_uploaded_bills_file_size"),
        UniqueConstraint("id", "user_id", name="uq_uploaded_bills_id_user"),
    )


class CarbonAnalysis(Timestamps, Base):
    __tablename__ = "carbon_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bill_id: Mapped[int] = mapped_column(index=True)
    provider_id: Mapped[int | None] = mapped_column(ForeignKey("cloud_providers.id", ondelete="SET NULL"), index=True)
    total_co2e: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    methodology_version: Mapped[str] = mapped_column(String(120))

    bill: Mapped[UploadedBill] = relationship()
    provider: Mapped[CloudProvider | None] = relationship()

    __table_args__ = (
        # An analysis cannot claim a different owner from its bill.
        ForeignKeyConstraint(
            ["bill_id", "user_id"], ["uploaded_bills.id", "uploaded_bills.user_id"],
            name="fk_carbon_analyses_bill_owner", ondelete="CASCADE",
        ),
        CheckConstraint("total_co2e >= 0", name="ck_carbon_analyses_co2e"),
    )


class CarbonAnalysisItem(Base):
    __tablename__ = "carbon_analysis_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("carbon_analyses.id", ondelete="CASCADE"), index=True)
    service_name: Mapped[str] = mapped_column(String(255))
    service_category: Mapped[str | None] = mapped_column(String(120))
    region: Mapped[str | None] = mapped_column(String(120))
    usage_quantity: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    usage_unit: Mapped[str | None] = mapped_column(String(80))
    estimated_co2e: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    analysis: Mapped[CarbonAnalysis] = relationship()

    __table_args__ = (
        CheckConstraint("usage_quantity >= 0", name="ck_carbon_analysis_items_usage"),
        CheckConstraint("estimated_co2e >= 0", name="ck_carbon_analysis_items_co2e"),
    )

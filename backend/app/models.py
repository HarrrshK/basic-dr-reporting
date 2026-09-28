from __future__ import annotations

from datetime import date, datetime, time
from enum import Enum

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Enum as SQLEnum, ForeignKey, Index, Integer, String, Text, Time, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class FollowUpStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"


class Area(Base):
    __tablename__ = "areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    doctors: Mapped[list[Doctor]] = relationship(back_populates="area")


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (
        Index("ix_doctor_name_area", "normalized_name", "area_id"),
        Index("ix_doctor_mobile", "normalized_mobile"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(240), index=True)
    normalized_name: Mapped[str] = mapped_column(String(240), index=True)
    existing_specialty: Mapped[str | None] = mapped_column(String(240))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="SET NULL"))
    hq: Mapped[str | None] = mapped_column(String(160), index=True)
    category: Mapped[str | None] = mapped_column(String(100), index=True)
    mobile: Mapped[str | None] = mapped_column(String(40))
    normalized_mobile: Mapped[str | None] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    specialty_group: Mapped[str | None] = mapped_column(String(160), index=True)
    doctor_status: Mapped[str | None] = mapped_column(String(100), index=True)
    qualification: Mapped[str | None] = mapped_column(String(160), index=True)
    gender: Mapped[str | None] = mapped_column(String(40), index=True)
    clinic_hospital: Mapped[str | None] = mapped_column(String(240), index=True)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    area: Mapped[Area | None] = relationship(back_populates="doctors")
    visits: Mapped[list[Visit]] = relationship(back_populates="doctor", cascade="save-update, merge")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    visits: Mapped[list[Visit]] = relationship(secondary="visit_products", back_populates="products")


class VisitProduct(Base):
    __tablename__ = "visit_products"
    __table_args__ = (UniqueConstraint("visit_id", "product_id"),)
    visit_id: Mapped[int] = mapped_column(ForeignKey("visits.id", ondelete="CASCADE"), primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="RESTRICT"), primary_key=True)


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (Index("ix_visit_doctor_date", "doctor_id", "visit_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id", ondelete="RESTRICT"), index=True)
    visit_date: Mapped[date] = mapped_column(Date, index=True)
    visit_time: Mapped[time | None] = mapped_column(Time)
    purpose: Mapped[str | None] = mapped_column(String(240), index=True)
    outcome: Mapped[str | None] = mapped_column(String(240), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    follow_up_date: Mapped[date | None] = mapped_column(Date, index=True)
    follow_up_reason: Mapped[str | None] = mapped_column(Text)
    follow_up_status: Mapped[FollowUpStatus | None] = mapped_column(SQLEnum(FollowUpStatus))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    doctor: Mapped[Doctor] = relationship(back_populates="visits")
    products: Mapped[list[Product]] = relationship(secondary="visit_products", back_populates="visits")


class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    file_hash: Mapped[str] = mapped_column(String(64), index=True)
    mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    rows: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="preview")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BackupChange(Base):
    __tablename__ = "backup_changes"
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(80), index=True)
    record_id: Mapped[str] = mapped_column(String(160))
    operation: Mapped[str] = mapped_column(String(16))
    row_data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BackupAgentState(Base):
    __tablename__ = "backup_agent_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    bootstrap_complete: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_backup: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_external_backup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

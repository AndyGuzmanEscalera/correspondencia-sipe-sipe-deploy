import uuid
from datetime import date, datetime

from sqlalchemy import (Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
                        Integer, String, Text, func, text)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class Device(Base):
    __tablename__ = "biometric_devices"
    device_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    ip: Mapped[str] = mapped_column(String(45))
    port: Mapped[int] = mapped_column(Integer)
    serial: Mapped[str | None] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Sync(Base):
    __tablename__ = "biometric_syncs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    device_id: Mapped[str] = mapped_column(ForeignKey("biometric_devices.device_id"))
    requested_start_date: Mapped[date] = mapped_column(Date)
    requested_end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    downloaded_records: Mapped[int] = mapped_column(Integer, default=0)
    new_records: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(50))
    error_message: Mapped[str | None] = mapped_column(Text)
    executed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    __table_args__ = (
        CheckConstraint("requested_start_date <= requested_end_date", name="ck_biometric_sync_range"),
        CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_biometric_sync_status"),
        Index("uq_biometric_sync_active", "device_id", unique=True,
              postgresql_where=text("status IN ('queued','running')")),
    )


class EmployeeLink(Base):
    __tablename__ = "biometric_employee_links"
    device_id: Mapped[str] = mapped_column(ForeignKey("biometric_devices.device_id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    funcionario_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"))


class Record(Base):
    __tablename__ = "biometric_records"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    record_id: Mapped[str] = mapped_column(String(64), unique=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("biometric_devices.device_id"))
    user_id: Mapped[str] = mapped_column(String(100))
    funcionario_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id", ondelete="SET NULL"))
    # Device wall clock: deliberately no timezone conversion.
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    status: Mapped[int | None] = mapped_column(Integer)
    punch: Mapped[int | None] = mapped_column(Integer)
    uid: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("source IN ('SOURCE_API','SOURCE_FILE')", name="ck_biometric_record_source"),
        Index("ix_biometric_employee_timestamp", "funcionario_id", "timestamp", "record_id"),
        Index("ix_biometric_device_timestamp", "device_id", "timestamp"),
    )


class Parameters(Base):
    __tablename__ = "attendance_parameters"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    morning_entry_tolerance: Mapped[int | None] = mapped_column(Integer)
    morning_exit_tolerance: Mapped[int | None] = mapped_column(Integer)
    afternoon_entry_tolerance: Mapped[int | None] = mapped_column(Integer)
    afternoon_exit_tolerance: Mapped[int | None] = mapped_column(Integer)
    minimum_return_interval: Mapped[int | None] = mapped_column(Integer)
    duplicate_window: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    __table_args__ = (CheckConstraint("id = 1", name="ck_attendance_parameters_singleton"),) + tuple(
        CheckConstraint(f"{name} BETWEEN 0 AND 86400", name=f"ck_attendance_{name}")
        for name in ("morning_entry_tolerance", "morning_exit_tolerance", "afternoon_entry_tolerance",
                     "afternoon_exit_tolerance", "minimum_return_interval", "duplicate_window")
    )


class ContractPolicy(Base):
    """Period configuration only; never duplicates employees or shift catalogs."""
    __tablename__ = "attendance_contract_policies"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    cutoff_day: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (CheckConstraint("cutoff_day BETWEEN 1 AND 31", name="ck_attendance_cutoff"),)

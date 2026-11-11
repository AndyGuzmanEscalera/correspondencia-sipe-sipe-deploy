import uuid
from datetime import datetime, date, time

from sqlalchemy import (
    Boolean,
    DateTime,
    Date,
    Time,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Correspondence(Base):
    __tablename__ = "correspondences"
    __table_args__ = (
        UniqueConstraint("route_number", name="uq_correspondences_route_number"),
        UniqueConstraint(
            "route_year",
            "route_sequence",
            name="uq_correspondences_route_year_sequence",
        ),
        UniqueConstraint(
            "document_type_id",
            "document_year",
            "document_sequence",
            name="uq_correspondences_document_type_year_sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    route_number: Mapped[str] = mapped_column(String(30), nullable=False)
    route_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    route_year: Mapped[int] = mapped_column(Integer, nullable=False)
    correspondence_type: Mapped[str] = mapped_column(String(20), nullable=False)
    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_types.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    document_snapshot: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    institutional_authority_snapshot: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    emission_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    emission_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    sender_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    sender_document: Mapped[str | None] = mapped_column(String(30), nullable=True)
    origin_external_document_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin_external_document_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)
    sender_contact: Mapped[str | None] = mapped_column(String(50), nullable=True)
    origin_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    origin_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizational_units.id", ondelete="RESTRICT"),
        nullable=True,
    )
    origin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    origin_employee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("employees.id", ondelete="RESTRICT"),
        nullable=True,
    )
    current_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizational_units.id", ondelete="RESTRICT"),
        nullable=True,
    )
    current_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    cite: Mapped[str | None] = mapped_column(String(100), nullable=True)
    cite_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cite_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deleted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    deletion_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    concluded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    reopened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

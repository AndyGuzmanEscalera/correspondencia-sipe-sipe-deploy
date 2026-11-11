import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.core.database import Base


class DocumentType(Base):
    __tablename__ = "document_types"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    # One persisted field; profile remains an alias for historical API clients.
    behavior: Mapped[str] = mapped_column(
        "profile",
        String(40),
        nullable=False,
        default="GENERIC",
        server_default="GENERIC",
    )
    profile = synonym("behavior")
    is_operational: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    creation_permission: Mapped[str | None] = mapped_column(String(100), nullable=True)
    minimum_hierarchy_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("position_hierarchies.id", ondelete="RESTRICT"),
        nullable=True,
    )
    code: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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

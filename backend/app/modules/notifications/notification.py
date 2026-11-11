import uuid
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("movement_id", "user_id", name="uq_notifications_movement_user"),
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    correspondence_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("correspondences.id", ondelete="SET NULL"))
    movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("correspondence_movements.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(String(30), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

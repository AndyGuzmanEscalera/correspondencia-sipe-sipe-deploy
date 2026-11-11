import uuid

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CorrespondenceDocumentSequence(Base):
    __tablename__ = "correspondence_document_sequences"

    document_type_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_types.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

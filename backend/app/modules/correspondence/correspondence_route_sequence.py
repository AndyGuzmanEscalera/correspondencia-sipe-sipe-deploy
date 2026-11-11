from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class CorrespondenceRouteSequence(Base):
    __tablename__ = "correspondence_route_sequences"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

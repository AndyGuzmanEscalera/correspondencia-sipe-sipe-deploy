"""Citizen tracking, independent of authenticated search and internal DTOs."""
import hashlib
import hmac
import re
import threading
import time
from collections import OrderedDict, deque

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.modules.correspondence.correspondence import Correspondence
from app.modules.correspondence.correspondence_movement import CorrespondenceMovement
from app.modules.organization.organizational_unit import OrganizationalUnit

NOT_FOUND = {"found": False, "message": "No se encontró una Hoja de Ruta con los datos ingresados."}


def normalize_document(value: str) -> str:
    value = value.strip().upper()
    value = re.sub(r"\s+(?:CBBA|CB|LP|SC|OR|PT|CH|TJ|BN|PD)$", "", value)
    if not re.fullmatch(r"[A-Z0-9.\s-]{4,30}", value):
        return ""
    value = re.sub(r"[.\s-]", "", value)
    return value if 4 <= len(value) <= 24 else ""


def document_digest(value: str, secret: str) -> str:
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


class PublicTrackingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    route_sheet_number: str = Field(max_length=60, repr=False)
    # Accepted for older clients only; never used for public lookup.
    document_number: str | None = Field(default=None, max_length=30, repr=False)


class PublicTrackingMovement(BaseModel):
    date: str
    status: str
    label: str
    unit: str | None = None


class PublicTrackingResponse(BaseModel):
    found: bool
    message: str | None = None
    route_sheet_number: str | None = None
    status: str | None = None
    status_label: str | None = None
    reference: str | None = None
    received_at: str | None = None
    current_unit: str | None = None
    last_updated_at: str | None = None
    movements: list[PublicTrackingMovement] | None = None


class TrackingRateLimiter:
    """Bounded process-local limiter. Never trust arbitrary X-Forwarded-For."""
    def __init__(self, limit=10, window=60, max_clients=10000, clock=time.monotonic):
        self.limit, self.window, self.max_clients, self.clock = limit, window, max_clients, clock
        self.clients = OrderedDict()
        self.lock = threading.Lock()

    def check(self, peer):
        now = self.clock()
        with self.lock:
            while self.clients and next(iter(self.clients.values()))[-1] <= now - self.window:
                self.clients.popitem(last=False)
            if peer not in self.clients and len(self.clients) >= self.max_clients:
                raise HTTPException(429, "Demasiados intentos. Intente nuevamente más tarde.", headers={"Retry-After": str(self.window)})
            attempts = self.clients.setdefault(peer, deque())
            while attempts and attempts[0] <= now - self.window:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(429, "Demasiados intentos. Intente nuevamente más tarde.", headers={"Retry-After": str(self.window)})
            attempts.append(now)
            self.clients.move_to_end(peer)


PUBLIC_EVENTS = {
    "CREATED": ("RECEIVED", "Recibido"), "RECEIVED": ("RECEIVED", "Recibido"),
    "DERIVED": ("DERIVED", "Derivado"), "IN_PROGRESS": ("IN_PROGRESS", "En trámite"),
    "CONCLUDED": ("CONCLUDED", "Concluido"), "REOPENED": ("REOPENED", "Reabierto"),
}


class PublicTrackingService:
    def __init__(self, db):
        self.db = db

    def track(self, body: PublicTrackingRequest):
        number = body.route_sheet_number.strip().upper()
        valid = bool(re.fullmatch(r"HR-\d{4}-\d{6,10}", number))
        row = self.db.scalar(select(Correspondence).where(
            Correspondence.route_number == (number if valid else ""),
            Correspondence.is_active.is_(True), Correspondence.deleted_at.is_(None),
        ))
        snapshot = (row.document_snapshot or {}) if row else {}
        if not (valid and row and row.correspondence_type == "EXTERNAL"
                and row.origin_employee_id is None and snapshot.get("behavior") == "ROUTE_SHEET"):
            return NOT_FOUND
        movements = self.db.scalars(select(CorrespondenceMovement).where(
            CorrespondenceMovement.correspondence_id == row.id,
            CorrespondenceMovement.cancelled_at.is_(None),
        ).order_by(CorrespondenceMovement.created_at, CorrespondenceMovement.sequence_number)).all()
        def unit_name(unit_id):
            unit = self.db.get(OrganizationalUnit, unit_id) if unit_id else None
            return unit.name if unit else None
        public = []
        for event in movements:
            mapped = PUBLIC_EVENTS.get(event.movement_type)
            if mapped:
                public.append(PublicTrackingMovement(date=event.created_at.isoformat(), status=mapped[0],
                    label=mapped[1], unit=unit_name(event.to_unit_id)))
        status, label = (public[-1].status, public[-1].label) if public else ("RECEIVED", "Recibido")
        if row.status == "CONCLUDED":
            status, label = "CONCLUDED", "Concluido"
        return PublicTrackingResponse(found=True, route_sheet_number=row.route_number,
            status=status, status_label=label, reference=row.reference,
            received_at=row.created_at.isoformat(), current_unit=unit_name(row.current_unit_id),
            last_updated_at=(movements[-1].created_at if movements else row.created_at).isoformat(),
            movements=public)

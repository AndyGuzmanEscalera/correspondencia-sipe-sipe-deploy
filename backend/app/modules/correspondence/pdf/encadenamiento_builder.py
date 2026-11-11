"""Construye el contexto del PDF desde correspondencia y movimientos."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.modules.correspondence.constants import (
    CORRESPONDENCE_TYPE_EXTERNAL,
    MOVEMENT_DERIVED,
    PRIORITY_HIGH,
)
from app.modules.correspondence.correspondence import Correspondence
from app.modules.correspondence.pdf.encadenamiento_pdf import (
    EncadenamientoMovementRow,
    EncadenamientoPdfContext,
    MOVEMENT_TYPE_LABELS,
)


def build_encadenamiento_context(
    *,
    correspondence: Correspondence,
    movement_rows: list[EncadenamientoMovementRow],
    para: str,
    de: str,
    issued_at: datetime,
    generated_at: datetime,
    logo_path: Path | None,
) -> EncadenamientoPdfContext:
    referencia = _resolve_referencia(correspondence)

    document_number = correspondence.document_number or str(
        correspondence.document_sequence or ""
    )

    return EncadenamientoPdfContext(
        route_number=correspondence.route_number,
        route_sequence=correspondence.route_sequence,
        document_number=document_number,
        correspondence_type=correspondence.correspondence_type,
        para=para,
        de=de,
        referencia=referencia,
        issued_at=issued_at,
        generated_at=generated_at,
        # No domain field stores the number of physical sheets/fojas.
        # Keep the printed slot intentionally unknown; attachments are not sheets.
        page_count_label="—",
        is_urgent=correspondence.priority == PRIORITY_HIGH,
        movements=tuple(movement_rows),
        logo_path=logo_path,
    )


def resolve_para(
    *,
    current_unit_name: str | None,
    current_user_name: str | None,
    movement_rows: list[EncadenamientoMovementRow],
    mayor_para: str | None = None,
) -> str:
    if mayor_para and mayor_para.strip():
        return mayor_para.strip()

    derived = [
        movement
        for movement in movement_rows
        if movement.movement_type == MOVEMENT_DERIVED and not movement.is_cancelled
    ]
    if derived:
        target = derived[0]
        return _format_party(target.to_unit, target.to_user)

    if current_unit_name or current_user_name:
        return _format_party(current_unit_name, current_user_name)

    for movement in movement_rows:
        if movement.to_unit or movement.to_user:
            return _format_party(movement.to_unit, movement.to_user)

    return ""


def resolve_de(
    *,
    correspondence: Correspondence,
    origin_employee_name: str | None,
    origin_unit_name: str | None,
    origin_position_name: str | None,
) -> str:
    if correspondence.correspondence_type == CORRESPONDENCE_TYPE_EXTERNAL:
        parts: list[str] = []
        if correspondence.sender_name:
            parts.append(correspondence.sender_name.strip())
        if correspondence.origin_description:
            parts.append(correspondence.origin_description.strip())
        return " — ".join(parts)

    parts: list[str] = []
    if origin_employee_name:
        parts.append(origin_employee_name)
    detail: list[str] = []
    if origin_position_name:
        detail.append(origin_position_name)
    if origin_unit_name:
        detail.append(origin_unit_name)
    if detail:
        parts.append(f"({', '.join(detail)})")
    return " ".join(parts).strip()


def movement_type_label(movement_type: str) -> str:
    return MOVEMENT_TYPE_LABELS.get(movement_type, movement_type)


def _resolve_referencia(correspondence: Correspondence) -> str:
    if correspondence.reference and correspondence.reference.strip():
        return correspondence.reference.strip()
    return correspondence.subject.strip()


def _format_party(unit: str | None, user: str | None) -> str:
    unit = (unit or "").strip()
    user = (user or "").strip()
    if unit and user:
        return f"{unit} — {user}"
    return unit or user

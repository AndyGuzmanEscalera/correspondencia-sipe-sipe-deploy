"""Route sheets use the existing Legal institutional routing form."""

from datetime import date, datetime, time

from app.core.institutional_time import BOLIVIA

from app.modules.correspondence.pdf.encadenamiento_pdf import (
    EncadenamientoMovementRow,
    EncadenamientoPdfContext,
    generate_encadenamiento_pdf,
)


def route_sheet_context(snapshot, *, movement_rows=None):
    issued_at = datetime.fromisoformat(snapshot["issued_at"])
    if snapshot.get("emission_date") and snapshot.get("emission_time"):
        issued_at = datetime.combine(
            date.fromisoformat(snapshot["emission_date"]),
            time.fromisoformat(snapshot["emission_time"]),
            tzinfo=BOLIVIA,
        )
    origin = snapshot["origin"]
    origin_label = " · ".join(
        filter(
            None,
            [
                origin.get("employee_name"),
                origin.get("position_name"),
                origin.get("unit_name"),
            ],
        )
    )
    authority = snapshot.get("authority") or {}
    if movement_rows is None:
        # Empty later boxes are writing spaces, never invented derivations.
        destination = authority.get("initial_recipient") or snapshot["destination"]
        movement_rows = [EncadenamientoMovementRow(
            sequence_number=2, movement_type="DERIVED", movement_type_label="Derivación",
            from_unit=None, from_user=None,
            to_unit=destination.get("unit_name"),
            to_user=destination.get("employee_name") or None,
            instruction=snapshot.get("initial_instruction"), created_at=issued_at,
            is_cancelled=False, cancellation_reason=None,
        )]
    return EncadenamientoPdfContext(
        route_number=snapshot["route_number"], route_sequence=0,
        document_number=snapshot["document_number"],
        correspondence_type="EXTERNAL" if origin.get("external") else "INTERNAL",
        para=authority.get("para_label") or "", de=origin_label,
        referencia=snapshot.get("reference") or "",
        issued_at=issued_at, generated_at=issued_at,
        page_count_label=str(snapshot["page_count"]),
        is_urgent=snapshot.get("priority") == "HIGH",
        movements=tuple(movement_rows), form_title="HOJA DE RUTA",
        content=snapshot.get("content"),
        refined_layout=True,
    )


def generate_route_sheet_pdf(snapshot, *, page_observer=None, movement_rows=None):
    return generate_encadenamiento_pdf(
        route_sheet_context(snapshot, movement_rows=movement_rows),
        page_observer=page_observer,
    )


def count_route_sheet_pages(snapshot, authority_snapshot, *, movement_rows=None):
    """Count the actual form pages, including long content and real movements."""
    pages = 1
    for _ in range(5):
        rendered_pages = []
        generate_route_sheet_pdf(
            {**snapshot, "authority": authority_snapshot, "page_count": pages},
            movement_rows=movement_rows,
            page_observer=rendered_pages.append,
        )
        actual = max(rendered_pages)
        if actual == pages:
            return actual
        pages = actual
    raise RuntimeError("No se pudo determinar el número de hojas de la Hoja de Ruta")

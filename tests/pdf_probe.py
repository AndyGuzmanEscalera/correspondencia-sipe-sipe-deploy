"""Stream into the immutable backend container: synthetic data, no DB or host fonts."""
from datetime import datetime, timezone
from pathlib import Path

from app.core.branding import OFFICIAL_CREST
from app.modules.correspondence.pdf.document_renderer import generate_document_pdf
from app.modules.correspondence.pdf.encadenamiento_pdf import (
    EncadenamientoPdfContext, generate_encadenamiento_pdf,
)
from app.modules.correspondence.pdf.report_pdf import generate_report_pdf

assert OFFICIAL_CREST.is_file() and OFFICIAL_CREST.read_bytes().startswith(b"\x89PNG")
party = dict(employee_name="Persona sintética", position_name="Cargo de prueba", unit_name="Unidad de prueba")
snapshot = dict(
    template="institutional-document-v1", issued_at="2026-11-11T12:00:00+00:00",
    name="Prueba", document_number="1", document_year=2026, route_number="HR-TEST-000001",
    origin=party, destination=party, actor=party, subject="Prueba de imagen autónoma",
    reference="Referencia sintética", content="Contenido sintético. Sin datos municipales.",
    page_count=1, priority="LOW",
)
for behavior in ("ROUTE_SHEET", "INTERNAL_COMMUNICATION", "REPORT", "MEMORANDUM", "CIRCULAR"):
    pdf = generate_document_pdf({**snapshot, "behavior": behavior})
    assert pdf.startswith(b"%PDF") and b"%%EOF" in pdf[-50:] and len(pdf) > 1500
    Path("/tmp/" + behavior + ".pdf").write_bytes(pdf)
    print(behavior + ": PDF OK")
now = datetime.now(timezone.utc)
pdf = generate_encadenamiento_pdf(EncadenamientoPdfContext(
    route_number="HR-TEST-000001", route_sequence=1, document_number="1",
    correspondence_type="EXTERNAL", para="Unidad sintética", de="Remitente sintético",
    referencia="Prueba de imagen", issued_at=now, generated_at=now,
))
assert pdf.startswith(b"%PDF") and b"%%EOF" in pdf[-50:]
Path("/tmp/LEGACY_CHAINING.pdf").write_bytes(pdf)
pdf = generate_report_pdf([("Periodo", "Prueba")], {"total": 0, "pending": 0, "concluded": 0}, [])
assert pdf.startswith(b"%PDF")
Path("/tmp/REPORT_EXPORT.pdf").write_bytes(pdf)
print("LEGACY_CHAINING y exportación: PDF OK")

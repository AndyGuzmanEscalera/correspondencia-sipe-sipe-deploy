"""Document presentation profiles; authorization is defined separately."""

from app.modules.correspondence.document_behavior import (
    DocumentBehavior,
    legacy_behavior,
)

DocumentFormProfile = DocumentBehavior


# Structural seed contracts. Names are presentation only; ranks live in DB.
OPERATIONAL_DOCUMENTS = {
    "COMUNICADO_INTERNO": (
        "Comunicado interno",
        DocumentBehavior.INTERNAL_COMMUNICATION,
        "correspondence.create.internal_communication",
        None,
    ),
    "INFORME": (
        "Informe",
        DocumentBehavior.REPORT,
        "correspondence.create.report",
        None,
    ),
    "MEMORANDUM": (
        "Memorándum",
        DocumentBehavior.MEMORANDUM,
        "correspondence.create.memorandum",
        "JEFE",
    ),
    "CIRCULAR": (
        "Circular",
        DocumentBehavior.CIRCULAR,
        "correspondence.create.circular",
        "DIRECTOR",
    ),
}
STRUCTURAL_DOCUMENTS = {
    **OPERATIONAL_DOCUMENTS,
    "HOJA_RUTA": (
        "Hoja de Ruta",
        DocumentBehavior.ROUTE_SHEET,
        "correspondence.create",
        None,
    ),
}
CREATION_PERMISSIONS = frozenset(row[2] for row in STRUCTURAL_DOCUMENTS.values())


def normalize_document_type_code(code):
    return code.strip().upper()


def resolve_document_form_profile(code, profile=None):
    """Old integration adapter; application policies read persisted behavior."""
    return legacy_behavior(code, profile)


def supports_encadenamiento_pdf(code):
    """Compatibility helper for old integrations; PDF service uses behavior."""
    return legacy_behavior(code) == DocumentBehavior.LEGACY_CHAINING


def is_internal_content_profile(profile):
    return profile in {
        DocumentBehavior.INTERNAL_COMMUNICATION,
        DocumentBehavior.REPORT,
        DocumentBehavior.MEMORANDUM,
        DocumentBehavior.CIRCULAR,
        DocumentBehavior.INTERNAL_NOTE,
    }

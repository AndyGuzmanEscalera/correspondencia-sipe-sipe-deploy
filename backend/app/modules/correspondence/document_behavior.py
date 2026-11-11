"""Stable document semantics. Display names and permissions are separate contracts."""

from enum import Enum


class DocumentBehavior(str, Enum):
    ROUTE_SHEET = "ROUTE_SHEET"
    INTERNAL_COMMUNICATION = "INTERNAL_COMMUNICATION"
    REPORT = "REPORT"
    MEMORANDUM = "MEMORANDUM"
    CIRCULAR = "CIRCULAR"
    GENERIC = "GENERIC"
    LEGACY_CHAINING = "LEGACY_CHAINING"
    LEGACY_INTERNAL_NOTE = "LEGACY_INTERNAL_NOTE"
    # Source compatibility for older integrations; no extra wire values.
    TECHNICAL_REPORT = "REPORT"
    CHAINING = "LEGACY_CHAINING"
    INTERNAL_NOTE = "LEGACY_INTERNAL_NOTE"


# Used only by migrations and the documented old-client/snapshot adapter.
CODE_BEHAVIORS = {
    "HOJA_RUTA": DocumentBehavior.ROUTE_SHEET,
    "COMUNICADO_INTERNO": DocumentBehavior.INTERNAL_COMMUNICATION,
    "INFORME": DocumentBehavior.REPORT,
    "MEMORANDUM": DocumentBehavior.MEMORANDUM,
    "CIRCULAR": DocumentBehavior.CIRCULAR,
    "EDIE": DocumentBehavior.LEGACY_CHAINING,
    "S": DocumentBehavior.LEGACY_CHAINING,
    "ENCADENAMIENTO DE DOCUMENTOS INTERNOS Y EXTERNOS": DocumentBehavior.LEGACY_CHAINING,
    "NOTA": DocumentBehavior.LEGACY_INTERNAL_NOTE,
}


def legacy_behavior(code, profile=None):
    """Compatibility only: persisted behavior is authoritative, including GENERIC."""
    if profile and profile != "GENERIC":
        return DocumentBehavior(profile)
    return CODE_BEHAVIORS.get(code.strip().upper(), DocumentBehavior.GENERIC)


def behavior_of(document_type):
    return DocumentBehavior(document_type.behavior)

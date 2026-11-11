"""Commit helpers that map DB uniqueness violations to functional 409 responses."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

CONSTRAINT_VALUE_KEYS: dict[str, str] = {
    "uq_organizational_units_code_lower": "code",
    "uq_positions_code_lower": "code",
    "uq_document_types_code_lower": "code",
    "document_types_code_key": "code",
    "uq_employees_document_number_lower": "document_number",
    "uq_users_username_lower": "username",
    "uq_users_email_lower": "email",
}

CONSTRAINT_MESSAGES: dict[str, str] = {
    "uq_organizational_units_code_lower": "Ya existe una unidad con el código {value}.",
    "uq_positions_code_lower": "Ya existe un cargo con el código {value}.",
    "uq_document_types_code_lower": "Ya existe un tipo de documento con el código {value}.",
    "document_types_code_key": "Ya existe un tipo de documento con el código {value}.",
    "uq_employees_document_number_lower": (
        "Ya existe un funcionario con el documento {value}."
    ),
    "uq_users_username_lower": "Ya existe un usuario con el nombre {value}.",
    "uq_users_email_lower": "Ya existe un usuario con el correo {value}.",
    "users_employee_id_key": "El funcionario ya tiene una cuenta de usuario vinculada.",
}


def conflict(message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def flush_or_conflict(db: Session, *, values: dict[str, str] | None = None) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise map_integrity_error(exc, values or {}) from exc


def commit_or_conflict(db: Session, *, values: dict[str, str] | None = None) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise map_integrity_error(exc, values or {}) from exc


def map_integrity_error(
    exc: IntegrityError,
    values: dict[str, str],
) -> HTTPException:
    constraint = _constraint_name(exc)
    template = CONSTRAINT_MESSAGES.get(constraint)
    if template is None:
        return conflict("El registro entraría en conflicto con datos existentes.")

    if "{value}" not in template:
        return conflict(template)

    value_key = CONSTRAINT_VALUE_KEYS.get(constraint, "value")
    value = values.get(value_key, "").strip()
    if not value:
        value = "indicado"
    return conflict(template.format(value=value))


def _constraint_name(exc: IntegrityError) -> str | None:
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    if constraint:
        return constraint

    message = str(orig or exc).lower()
    for known in CONSTRAINT_MESSAGES:
        if known.lower() in message:
            return known
    return None

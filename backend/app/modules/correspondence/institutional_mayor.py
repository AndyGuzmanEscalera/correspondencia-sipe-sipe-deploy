"""Resolución del alcalde/alcaldesa titular para encadenamiento (PARA / primer destinatario)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position
from app.modules.system.constants import INSTITUTIONAL_MAYOR_EMPLOYEE_ID, INSTITUTIONAL_MAYOR_DISPLAY_NAME
from app.modules.system.system_setting import SystemSetting


@dataclass(frozen=True, slots=True)
class InstitutionalMayor:
    user_id: uuid.UUID
    employee_id: uuid.UUID
    unit_id: uuid.UUID
    employee_name: str
    position_name: str
    unit_name: str
    document_number: str | None = None
    document_display_name: str | None = None

    @property
    def para_label(self) -> str:
        name = self.document_display_name or self.employee_name
        return f"{self.unit_name} — {name} ({self.position_name})"


def resolve_institutional_mayor(db: Session) -> InstitutionalMayor | None:
    setting = db.get(SystemSetting, INSTITUTIONAL_MAYOR_EMPLOYEE_ID)
    if setting is None or not setting.value:
        return None
    try:
        employee_id = uuid.UUID(setting.value)
    except ValueError:
        raise HTTPException(status_code=422, detail="La configuración del Alcalde/Máxima Autoridad Ejecutiva no es válida. Seleccione un funcionario válido en Configuración.")
    authority = resolve_employee_authority(db, employee_id)
    display = db.get(SystemSetting, INSTITUTIONAL_MAYOR_DISPLAY_NAME)
    name = display.value.strip() if display and display.value else None
    return replace(authority, document_display_name=name or None)


def resolve_employee_authority(db: Session, employee_id: uuid.UUID) -> InstitutionalMayor:
    """EDIE usa to_user_id: requiere funcionario, cargo, unidad y cuenta activos."""
    rows = db.execute(_active_authority_query().where(Employee.id == employee_id).limit(2)).mappings().all()
    if not rows:
        raise HTTPException(status_code=422, detail="El funcionario configurado como Alcalde/Máxima Autoridad Ejecutiva debe existir y tener funcionario, cargo, unidad y cuenta de usuario activos. Revise Configuración.")
    # users.employee_id es único en la BD; mantener defensa para datos inválidos.
    if len(rows) > 1:
        raise HTTPException(status_code=422, detail="El funcionario tiene más de una cuenta activa. Revise sus cuentas antes de configurar la autoridad institucional.")
    return _authority_from_row(rows[0])


def list_active_authorities(db: Session) -> list[InstitutionalMayor]:
    return [_authority_from_row(row) for row in db.execute(_active_authority_query()).mappings()]


def _active_authority_query():
    return (select(
            User.id.label("user_id"),
            Employee.id.label("employee_id"),
            Employee.unit_id.label("unit_id"),
            Employee.first_name,
            Employee.last_name,
            Employee.document_number,
            Position.name.label("position_name"),
            OrganizationalUnit.name.label("unit_name"),
        )
        .join(Employee, Employee.id == User.employee_id)
        .join(Position, Position.id == Employee.position_id)
        .join(OrganizationalUnit, OrganizationalUnit.id == Employee.unit_id)
        .where(
            User.is_active.is_(True),
            Employee.is_active.is_(True),
            Position.is_active.is_(True),
            OrganizationalUnit.is_active.is_(True),
        )
        .order_by(User.created_at)
    )


def _authority_from_row(row) -> InstitutionalMayor:
    first_name = (row["first_name"] or "").strip()
    last_name = (row["last_name"] or "").strip()
    employee_name = f"{first_name} {last_name}".strip()

    return InstitutionalMayor(
        user_id=row["user_id"],
        employee_id=row["employee_id"],
        unit_id=row["unit_id"],
        employee_name=employee_name,
        position_name=(row["position_name"] or "").strip(),
        unit_name=(row["unit_name"] or "").strip(),
        document_number=row["document_number"],
    )


def list_edie_first_recipients(db: Session) -> list[InstitutionalMayor]:
    from app.modules.correspondence.creation_policy import configured_creation_ids
    _, ids = configured_creation_ids(db)
    query = _active_authority_query().where(Employee.id.in_(ids))
    return [_authority_from_row(row) for row in db.execute(query).mappings()]


def resolve_edie_first_recipient(db: Session, employee_id: uuid.UUID | None) -> InstitutionalMayor:
    if employee_id is None:
        raise HTTPException(status_code=422, detail="Seleccione un receptor inicial de Alcaldía.")
    recipient = next((item for item in list_edie_first_recipients(db)
                      if item.employee_id == employee_id), None)
    if recipient is None:
        raise HTTPException(status_code=422, detail="El receptor inicial no está configurado o no tiene funcionario, cargo, unidad y cuenta activos.")
    return recipient


def require_document_authority_name(db: Session) -> str:
    setting = db.get(SystemSetting, INSTITUTIONAL_MAYOR_DISPLAY_NAME)
    name = setting.value.strip() if setting and setting.value else ""
    if not name:
        raise HTTPException(status_code=422, detail="Configure el nombre de la Máxima Autoridad Ejecutiva para los documentos en Configuración → Institucional.")
    return name

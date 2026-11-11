"""Política de creación basada en referencias institucionales y permisos."""
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import select

from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.system.system_setting import SystemSetting

INTAKE_UNIT_KEY = "institutional.intake_unit_id"
INITIAL_RECIPIENTS_KEY = "institutional.initial_recipient_employee_ids"


def require_create_permission(db, user):
    from app.modules.identity.rbac_service import RbacService
    from app.modules.correspondence.document_type_profiles import CREATION_PERMISSIONS
    if not (CREATION_PERMISSIONS & set(RbacService(db).get_effective_permission_codes(user.id))):
        raise HTTPException(status_code=403, detail="Permiso insuficiente para crear correspondencia")


def configured_creation_ids(db):
    intake = db.get(SystemSetting, INTAKE_UNIT_KEY)
    recipients = db.get(SystemSetting, INITIAL_RECIPIENTS_KEY)
    try:
        unit_id = uuid.UUID(intake.value) if intake and intake.value else None
        values = json.loads(recipients.value) if recipients and recipients.value else []
        if not isinstance(values, list):
            raise ValueError()
        ids = list(dict.fromkeys(uuid.UUID(value) for value in values))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=422, detail="La configuración de Ventanilla y receptores iniciales no es válida")
    return unit_id, ids


def require_intake_unit(db):
    unit_id, _ = configured_creation_ids(db)
    unit = db.get(OrganizationalUnit, unit_id) if unit_id else None
    if unit is None or not unit.is_active:
        raise HTTPException(status_code=422, detail="Configure una Unidad de Ventanilla existente y activa antes de crear correspondencia")
    return unit

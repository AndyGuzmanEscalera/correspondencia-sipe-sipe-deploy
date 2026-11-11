"""Protect administrator continuity using effective permissions."""

from fastapi import HTTPException
from sqlalchemy import select
from app.modules.identity.permission import Permission
from app.modules.identity.user import User
from app.modules.identity.rbac_service import RbacService
from app.modules.organization.employee import Employee
from app.modules.organization.position import Position
from app.modules.organization.organizational_unit import OrganizationalUnit

CRITICAL_PERMISSIONS = frozenset(
    {
        "master_data.users.manage",
        "master_data.roles.assign",
        "system.settings.manage",
    }
)


def _lock_administration(db):
    db.scalars(
        select(Permission)
        .where(Permission.code.in_(CRITICAL_PERMISSIONS))
        .order_by(Permission.code)
        .with_for_update()
    ).all()


def _valid_employee(db, employee_id):
    return (
        employee_id is None
        or db.scalar(
            select(Employee.id)
            .join(Position, Position.id == Employee.position_id)
            .join(OrganizationalUnit, OrganizationalUnit.id == Employee.unit_id)
            .where(
                Employee.id == employee_id,
                Employee.is_active.is_(True),
                Position.is_active.is_(True),
                OrganizationalUnit.is_active.is_(True),
            )
        )
        is not None
    )


def operative_administrators(db):
    rbac = RbacService(db)
    return {
        u.id
        for u in db.scalars(select(User).where(User.is_active.is_(True)))
        if _valid_employee(db, u.employee_id)
        and CRITICAL_PERMISSIONS <= set(rbac.get_effective_permission_codes(u.id))
    }


def _require_replacement(db, target_id, message):
    operators = operative_administrators(db)
    if target_id in operators and not (operators - {target_id}):
        raise HTTPException(status_code=409, detail=message)


def protect_deactivation(db, actor, target):
    if actor.id == target.id:
        raise HTTPException(
            status_code=409, detail="No puede desactivar su propia cuenta."
        )
    _lock_administration(db)
    db.refresh(target)
    _require_replacement(
        db,
        target.id,
        "No puede desactivar la última cuenta administradora operativa. Habilite otra cuenta con gestión de usuarios, roles y configuración.",
    )


def protect_role_change(
    db, target, role_ids, employee_id, *, direct_permission_ids=None
):
    _lock_administration(db)
    codes = set(
        RbacService(db).get_effective_permission_codes(
            target.id, role_ids=role_ids, direct_permission_ids=direct_permission_ids
        )
    )
    if not (CRITICAL_PERMISSIONS <= codes and _valid_employee(db, employee_id)):
        _require_replacement(
            db,
            target.id,
            "No puede retirar la capacidad administrativa de la última cuenta operativa. Habilite otra cuenta administradora antes de cambiar sus roles o permisos.",
        )

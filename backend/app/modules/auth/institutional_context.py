from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.auth.schemas import (
    MeEmployeeContext,
    MeInstitutionalHierarchy,
    MeInstitutionalPosition,
    MeInstitutionalUnit,
)
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position


def resolve_me_employee_context(
    db: Session,
    user: User,
) -> MeEmployeeContext | None:
    """Institutional context for /auth/me (display + client UX).

    Returns None when the user has no employee_id or the employee row is missing.
    Inactive position/unit rows are still returned with names when present in DB.
    Operational ownership rules remain in CorrespondenceService.
    """
    if user.employee_id is None:
        return None

    row = db.execute(
        select(Employee, Position, OrganizationalUnit)
        .outerjoin(Position, Employee.position_id == Position.id)
        .outerjoin(OrganizationalUnit, Employee.unit_id == OrganizationalUnit.id)
        .where(Employee.id == user.employee_id)
    ).one_or_none()

    if row is None:
        return None

    employee, position, unit = row[0], row[1], row[2]

    full_name = f"{employee.first_name} {employee.last_name}".strip()
    return MeEmployeeContext(
        id=str(employee.id),
        full_name=full_name,
        document_number=employee.document_number,
        position=_position_ref(position, db),
        unit=_unit_ref(unit),
    )


def _position_ref(position: Position | None, db) -> MeInstitutionalPosition | None:
    if position is None:
        return None
    from app.modules.organization.position_hierarchy import PositionHierarchy

    hierarchy = (
        db.get(PositionHierarchy, position.hierarchy_id)
        if position.hierarchy_id
        else None
    )
    return MeInstitutionalPosition(
        id=str(position.id),
        name=position.name,
        hierarchy=(
            MeInstitutionalHierarchy(
                id=str(hierarchy.id),
                code=hierarchy.code,
                name=hierarchy.name,
                rank=hierarchy.rank,
                is_active=hierarchy.is_active,
            )
            if hierarchy
            else None
        ),
    )


def _unit_ref(unit: OrganizationalUnit | None) -> MeInstitutionalUnit | None:
    if unit is None:
        return None
    return MeInstitutionalUnit(id=str(unit.id), name=unit.name)

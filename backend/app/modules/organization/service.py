import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.position import Position
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.schemas import OrganizationalUnitResponse, UnitUserResponse


class OrganizationService:
    """Read-only organization lookups for operational workflows."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_active_units(self) -> list[OrganizationalUnitResponse]:
        rows = self._db.scalars(
            select(OrganizationalUnit)
            .where(OrganizationalUnit.is_active == True)  # noqa: E712
            .order_by(OrganizationalUnit.name)
        ).all()
        return [
            OrganizationalUnitResponse(id=row.id, code=row.code, name=row.name)
            for row in rows
        ]

    def list_active_users_by_unit(
        self,
        unit_id: uuid.UUID,
    ) -> list[UnitUserResponse]:
        unit = self._db.get(OrganizationalUnit, unit_id)
        if unit is None or not unit.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unidad no encontrada",
            )

        rows = self._db.execute(
            select(User, Employee)
            .join(Employee, User.employee_id == Employee.id)
            .join(Position, Position.id == Employee.position_id)
            .where(
                Employee.unit_id == unit_id,
                Employee.is_active == True,  # noqa: E712
                Position.is_active.is_(True),
                User.is_active == True,  # noqa: E712
            )
            .order_by(Employee.last_name, Employee.first_name)
        ).all()

        return [
            UnitUserResponse(
                id=user.id,
                username=user.username,
                display_name=f"{employee.first_name} {employee.last_name}".strip(),
            )
            for user, employee in rows
        ]

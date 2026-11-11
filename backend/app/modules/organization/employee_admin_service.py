import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.audit import apply_audit
from app.common.master_data_deletion import validate_deletion, validate_employee_restoration
from app.common.db_commit import commit_or_conflict, conflict
from app.common.normalize import normalize_document_number
from app.common.schemas import PaginatedResponse, paginate
from app.modules.identity.user import User
from app.modules.organization.admin_schemas import (
    CreateEmployeeRequest,
    EmployeeAdminResponse,
    UpdateEmployeeRequest,
)
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position
from app.modules.organization.position_admin_service import PositionAdminService


class EmployeeAdminService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_employees(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        is_active: bool | None,
        unit_id: uuid.UUID | None,
        position_id: uuid.UUID | None,
        available_for_user: bool = False,
        except_user_id: uuid.UUID | None = None,
    ) -> PaginatedResponse[EmployeeAdminResponse]:
        query = select(Employee)
        if available_for_user:
            query = self._apply_available_for_user_filter(
                query,
                except_user_id=except_user_id,
            )
        if is_active is not None:
            query = query.where(Employee.is_active == is_active)
        if unit_id:
            query = query.where(Employee.unit_id == unit_id)
        if position_id:
            query = query.where(Employee.position_id == position_id)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                Employee.first_name.ilike(term)
                | Employee.last_name.ilike(term)
                | Employee.document_number.ilike(term)
            )
        total = self._db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        offset = (page - 1) * page_size
        rows = self._db.scalars(
            query.order_by(Employee.last_name, Employee.first_name)
            .offset(offset)
            .limit(page_size)
        ).all()
        items = [self._to_response(row) for row in rows]
        return paginate(items=items, total=total, page=page, page_size=page_size)

    def get_employee(self, employee_id: uuid.UUID) -> EmployeeAdminResponse:
        return self._to_response(self._get_or_404(employee_id))

    def create_employee(
        self,
        actor: User,
        body: CreateEmployeeRequest,
    ) -> EmployeeAdminResponse:
        self._validate_active_assignment(body.unit_id, body.position_id)
        document = normalize_document_number(body.document_number)
        self._ensure_document_unique(document)

        row = Employee(
            first_name=body.first_name.strip(),
            last_name=body.last_name.strip(),
            document_number=document,
            email=body.email.strip() if body.email else None,
            phone=body.phone.strip() if body.phone else None,
            unit_id=body.unit_id,
            position_id=body.position_id,
            is_active=True,
        )
        apply_audit(row, actor.id, is_create=True)
        self._db.add(row)
        commit_or_conflict(self._db, values={"document_number": document})
        self._db.refresh(row)
        return self._to_response(row)

    def update_employee(
        self,
        actor: User,
        employee_id: uuid.UUID,
        body: UpdateEmployeeRequest,
    ) -> EmployeeAdminResponse:
        row = self._get_or_404(employee_id)
        if row.is_active:
            self._validate_active_assignment(body.unit_id, body.position_id)
        document = normalize_document_number(body.document_number)
        self._ensure_document_unique(document, exclude_id=employee_id)

        row.first_name = body.first_name.strip()
        row.last_name = body.last_name.strip()
        row.document_number = document
        row.email = body.email.strip() if body.email else None
        row.phone = body.phone.strip() if body.phone else None
        row.unit_id = body.unit_id
        row.position_id = body.position_id
        apply_audit(row, actor.id)
        commit_or_conflict(self._db, values={"document_number": document})
        self._db.refresh(row)
        return self._to_response(row)

    def set_active(
        self,
        actor: User,
        employee_id: uuid.UUID,
        *,
        is_active: bool,
    ) -> EmployeeAdminResponse:
        row = self._get_or_404(employee_id)
        if not is_active and row.is_active:
            validate_deletion(self._db, 'employees', row)
        if is_active and not row.is_active:
            validate_employee_restoration(self._db, row)
        row.is_active = is_active
        apply_audit(row, actor.id)
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def _validate_active_assignment(
        self,
        unit_id: uuid.UUID,
        position_id: uuid.UUID,
    ) -> None:
        unit = self._db.get(OrganizationalUnit, unit_id)
        if unit is None or not unit.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unidad no encontrada o inactiva",
            )
        PositionAdminService(self._db).get_active_position(position_id)

    def _ensure_document_unique(
        self,
        document_number: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        query = select(Employee.id).where(
            func.lower(Employee.document_number) == document_number.lower()
        )
        if exclude_id:
            query = query.where(Employee.id != exclude_id)
        if self._db.scalar(query):
            raise conflict(f"Ya existe un funcionario con el documento {document_number}.")

    def _apply_available_for_user_filter(
        self,
        query,
        *,
        except_user_id: uuid.UUID | None,
    ):
        """Employees without a linked user, or linked only to except_user_id (edit)."""
        from sqlalchemy import exists, or_

        assigned = (
            select(User.id)
            .where(User.employee_id == Employee.id)
            .correlate(Employee)
        )
        if except_user_id is not None:
            current_user_employee = (
                select(User.id)
                .where(
                    User.employee_id == Employee.id,
                    User.id == except_user_id,
                )
                .correlate(Employee)
            )
            return query.where(
                or_(~exists(assigned), exists(current_user_employee))
            )
        return query.where(~exists(assigned))

    def _get_or_404(self, employee_id: uuid.UUID) -> Employee:
        row = self._db.get(Employee, employee_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Funcionario no encontrado",
            )
        return row

    def _to_response(self, row: Employee) -> EmployeeAdminResponse:
        unit_name = None
        position_name = None
        if row.unit_id:
            unit = self._db.get(OrganizationalUnit, row.unit_id)
            unit_name = unit.name if unit else None
        if row.position_id:
            position = self._db.get(Position, row.position_id)
            position_name = position.name if position else None
        return EmployeeAdminResponse(
            id=row.id,
            first_name=row.first_name,
            last_name=row.last_name,
            document_number=row.document_number,
            email=row.email,
            phone=row.phone,
            unit_id=row.unit_id,
            unit_name=unit_name,
            position_id=row.position_id,
            position_name=position_name,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by_user_id=row.created_by_user_id,
            updated_by_user_id=row.updated_by_user_id,
        )

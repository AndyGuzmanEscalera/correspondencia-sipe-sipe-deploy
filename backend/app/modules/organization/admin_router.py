import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.common.schemas import ActiveToggleRequest, PaginatedResponse
from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_permission
from app.modules.identity.user import User
from app.modules.organization.admin_schemas import (
    CreateEmployeeRequest,
    CreateOrganizationalUnitRequest,
    CreatePositionRequest,
    EmployeeAdminResponse,
    OrganizationalUnitAdminResponse,
    PositionAdminResponse,
    UpdateEmployeeRequest,
    UpdateOrganizationalUnitRequest,
    UpdatePositionRequest,
)
from app.modules.organization.employee_admin_service import EmployeeAdminService
from app.modules.organization.position_admin_service import PositionAdminService
from app.modules.organization.unit_admin_service import UnitAdminService

units_router = APIRouter(
    prefix="/admin/organizational-units",
    tags=["admin-organizational-units"],
)
positions_router = APIRouter(prefix="/admin/positions", tags=["admin-positions"])
employees_router = APIRouter(prefix="/admin/employees", tags=["admin-employees"])
from app.modules.organization.hierarchy_admin import router as hierarchies_router
positions_router.include_router(hierarchies_router)


def _units(db: Annotated[Session, Depends(get_db)]) -> UnitAdminService:
    return UnitAdminService(db)


def _positions(db: Annotated[Session, Depends(get_db)]) -> PositionAdminService:
    return PositionAdminService(db)


def _employees(db: Annotated[Session, Depends(get_db)]) -> EmployeeAdminService:
    return EmployeeAdminService(db)


@units_router.get("", response_model=PaginatedResponse[OrganizationalUnitAdminResponse])
def list_units_admin(
    service: Annotated[UnitAdminService, Depends(_units)],
    _user: Annotated[User, Depends(require_permission("master_data.organizational_units.read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    is_active: bool = True,
    include_deleted: bool = False,
) -> PaginatedResponse[OrganizationalUnitAdminResponse]:
    return service.list_units(
        page=page,
        page_size=page_size,
        search=search,
        is_active=None if include_deleted else is_active,
    )


@units_router.get("/{unit_id}", response_model=OrganizationalUnitAdminResponse)
def get_unit_admin(
    unit_id: uuid.UUID,
    service: Annotated[UnitAdminService, Depends(_units)],
    _user: Annotated[User, Depends(require_permission("master_data.organizational_units.read"))],
) -> OrganizationalUnitAdminResponse:
    return service.get_unit(unit_id)


@units_router.post(
    "",
    response_model=OrganizationalUnitAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_unit_admin(
    body: CreateOrganizationalUnitRequest,
    service: Annotated[UnitAdminService, Depends(_units)],
    current_user: Annotated[User, Depends(require_permission("master_data.organizational_units.manage"))],
) -> OrganizationalUnitAdminResponse:
    return service.create_unit(current_user, body)


@units_router.put("/{unit_id}", response_model=OrganizationalUnitAdminResponse)
def update_unit_admin(
    unit_id: uuid.UUID,
    body: UpdateOrganizationalUnitRequest,
    service: Annotated[UnitAdminService, Depends(_units)],
    current_user: Annotated[User, Depends(require_permission("master_data.organizational_units.manage"))],
) -> OrganizationalUnitAdminResponse:
    return service.update_unit(current_user, unit_id, body)


@units_router.patch("/{unit_id}/active", response_model=OrganizationalUnitAdminResponse)
def toggle_unit_admin(
    unit_id: uuid.UUID,
    body: ActiveToggleRequest,
    service: Annotated[UnitAdminService, Depends(_units)],
    current_user: Annotated[User, Depends(require_permission("master_data.organizational_units.manage"))],
) -> OrganizationalUnitAdminResponse:
    return service.set_active(current_user, unit_id, is_active=body.is_active)


@positions_router.get("", response_model=PaginatedResponse[PositionAdminResponse])
def list_positions_admin(
    service: Annotated[PositionAdminService, Depends(_positions)],
    _user: Annotated[User, Depends(require_permission("master_data.positions.read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    is_active: bool = True,
    include_deleted: bool = False,
) -> PaginatedResponse[PositionAdminResponse]:
    return service.list_positions(
        page=page,
        page_size=page_size,
        search=search,
        is_active=None if include_deleted else is_active,
    )


@positions_router.get("/{position_id}", response_model=PositionAdminResponse)
def get_position_admin(
    position_id: uuid.UUID,
    service: Annotated[PositionAdminService, Depends(_positions)],
    _user: Annotated[User, Depends(require_permission("master_data.positions.read"))],
) -> PositionAdminResponse:
    return service.get_position(position_id)


@positions_router.post(
    "",
    response_model=PositionAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_position_admin(
    body: CreatePositionRequest,
    service: Annotated[PositionAdminService, Depends(_positions)],
    current_user: Annotated[User, Depends(require_permission("master_data.positions.manage"))],
) -> PositionAdminResponse:
    return service.create_position(current_user, body)


@positions_router.put("/{position_id}", response_model=PositionAdminResponse)
def update_position_admin(
    position_id: uuid.UUID,
    body: UpdatePositionRequest,
    service: Annotated[PositionAdminService, Depends(_positions)],
    current_user: Annotated[User, Depends(require_permission("master_data.positions.manage"))],
) -> PositionAdminResponse:
    return service.update_position(current_user, position_id, body)


@positions_router.patch("/{position_id}/active", response_model=PositionAdminResponse)
def toggle_position_admin(
    position_id: uuid.UUID,
    body: ActiveToggleRequest,
    service: Annotated[PositionAdminService, Depends(_positions)],
    current_user: Annotated[User, Depends(require_permission("master_data.positions.manage"))],
) -> PositionAdminResponse:
    return service.set_active(current_user, position_id, is_active=body.is_active)


@employees_router.get("", response_model=PaginatedResponse[EmployeeAdminResponse])
def list_employees_admin(
    service: Annotated[EmployeeAdminService, Depends(_employees)],
    _user: Annotated[User, Depends(require_permission("master_data.employees.read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    is_active: bool = True,
    include_deleted: bool = False,
    unit_id: uuid.UUID | None = None,
    position_id: uuid.UUID | None = None,
    available_for_user: bool = False,
    except_user_id: uuid.UUID | None = None,
) -> PaginatedResponse[EmployeeAdminResponse]:
    return service.list_employees(
        page=page,
        page_size=page_size,
        search=search,
        is_active=None if include_deleted else is_active,
        unit_id=unit_id,
        position_id=position_id,
        available_for_user=available_for_user,
        except_user_id=except_user_id,
    )


@employees_router.get("/{employee_id}", response_model=EmployeeAdminResponse)
def get_employee_admin(
    employee_id: uuid.UUID,
    service: Annotated[EmployeeAdminService, Depends(_employees)],
    _user: Annotated[User, Depends(require_permission("master_data.employees.read"))],
) -> EmployeeAdminResponse:
    return service.get_employee(employee_id)


@employees_router.post(
    "",
    response_model=EmployeeAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_employee_admin(
    body: CreateEmployeeRequest,
    service: Annotated[EmployeeAdminService, Depends(_employees)],
    current_user: Annotated[User, Depends(require_permission("master_data.employees.manage"))],
) -> EmployeeAdminResponse:
    return service.create_employee(current_user, body)


@employees_router.put("/{employee_id}", response_model=EmployeeAdminResponse)
def update_employee_admin(
    employee_id: uuid.UUID,
    body: UpdateEmployeeRequest,
    service: Annotated[EmployeeAdminService, Depends(_employees)],
    current_user: Annotated[User, Depends(require_permission("master_data.employees.manage"))],
) -> EmployeeAdminResponse:
    return service.update_employee(current_user, employee_id, body)


@employees_router.patch("/{employee_id}/active", response_model=EmployeeAdminResponse)
def toggle_employee_admin(
    employee_id: uuid.UUID,
    body: ActiveToggleRequest,
    service: Annotated[EmployeeAdminService, Depends(_employees)],
    current_user: Annotated[User, Depends(require_permission("master_data.employees.manage"))],
) -> EmployeeAdminResponse:
    return service.set_active(current_user, employee_id, is_active=body.is_active)

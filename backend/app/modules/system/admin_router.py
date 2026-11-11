from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.identity.user import User
from app.modules.system.schemas import (
    InstitutionalSettingsResponse,
    UpdateInstitutionalSettingsRequest,
    AuthorityEmployeeResponse,
)
from app.modules.system.service import SystemSettingsService
from sqlalchemy import select
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.schemas import OrganizationalUnitResponse

router = APIRouter(
    prefix="/admin/system-settings",
    tags=["admin-system-settings"],
)


def _service(db: Annotated[Session, Depends(get_db)]) -> SystemSettingsService:
    return SystemSettingsService(db)


@router.get("/intake-units", response_model=list[OrganizationalUnitResponse])
def list_intake_units(
    db: Annotated[Session, Depends(get_db)],
    _user: Annotated[User, Depends(require_permission("system.settings.manage"))],
):
    return [OrganizationalUnitResponse(id=row.id, code=row.code, name=row.name)
            for row in db.scalars(select(OrganizationalUnit)
                .where(OrganizationalUnit.is_active.is_(True)).order_by(OrganizationalUnit.name))]


@router.get("/authority-employees", response_model=list[AuthorityEmployeeResponse])
def list_authority_employees(
    service: Annotated[SystemSettingsService, Depends(_service)],
    _user: Annotated[User, Depends(require_permission("system.settings.manage"))],
) -> list[AuthorityEmployeeResponse]:
    return service.list_authority_employees()


@router.get(
    "/institutional",
    response_model=InstitutionalSettingsResponse,
)
def get_institutional_settings(
    service: Annotated[SystemSettingsService, Depends(_service)],
    _user: Annotated[User, Depends(require_permission("system.settings.read"))],
) -> InstitutionalSettingsResponse:
    return service.get_institutional_settings()


@router.put(
    "/institutional",
    response_model=InstitutionalSettingsResponse,
)
def update_institutional_settings(
    body: UpdateInstitutionalSettingsRequest,
    service: Annotated[SystemSettingsService, Depends(_service)],
    current_user: Annotated[User, Depends(require_permission("system.settings.manage"))],
) -> InstitutionalSettingsResponse:
    return service.update_institutional_settings(current_user, body)

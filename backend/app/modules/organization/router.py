import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.identity.user import User
from app.modules.organization.schemas import OrganizationalUnitResponse, UnitUserResponse
from app.modules.organization.service import OrganizationService

router = APIRouter(prefix="/organizational-units", tags=["organization"])


def _service(db: Annotated[Session, Depends(get_db)]) -> OrganizationService:
    return OrganizationService(db)


@router.get("", response_model=list[OrganizationalUnitResponse])
def list_organizational_units(
    _user: Annotated[User, Depends(get_current_user)],
    service: Annotated[OrganizationService, Depends(_service)],
) -> list[OrganizationalUnitResponse]:
    return service.list_active_units()


@router.get("/{unit_id}/users", response_model=list[UnitUserResponse])
def list_unit_users(
    unit_id: uuid.UUID,
    _user: Annotated[User, Depends(get_current_user)],
    service: Annotated[OrganizationService, Depends(_service)],
) -> list[UnitUserResponse]:
    return service.list_active_users_by_unit(unit_id)

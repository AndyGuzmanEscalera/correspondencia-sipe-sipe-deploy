import uuid
from typing import Annotated
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.identity.permission import Permission
from app.modules.identity.rbac_service import RbacService
from app.modules.identity.user import User
from app.modules.identity.admin_schemas import UserAdminResponse
from app.modules.identity.user_admin_service import UserAdminService

router = APIRouter(tags=["admin-user-permissions"])
DB = Annotated[Session, Depends(get_db)]
Manager = Annotated[User, Depends(require_permission("master_data.users.manage"))]


class PermissionOption(BaseModel):
    id: uuid.UUID
    code: str
    description: str | None = None
    model_config = {"from_attributes": True}


class PermissionUpdate(BaseModel):
    permission_ids: list[uuid.UUID] = Field(max_length=1000)


@router.get("/permissions", response_model=list[PermissionOption])
def catalog(db: DB, user: Manager):
    RbacService(db).assert_can_manage_permissions(user, [])
    # Only grantable capabilities are offered. Unknown submitted IDs are still
    # rejected by the write service; the catalog is not a security boundary.
    return list(
        db.scalars(
            select(Permission)
            .where(
                Permission.code.in_(
                    RbacService(db).get_effective_permission_codes(user.id)
                )
            )
            .order_by(Permission.code)
        )
    )


@router.put("/users/{user_id}/permissions", response_model=UserAdminResponse)
def replace(user_id: uuid.UUID, body: PermissionUpdate, db: DB, user: Manager):
    return UserAdminService(db).set_direct_permissions(
        user, user_id, body.permission_ids
    )

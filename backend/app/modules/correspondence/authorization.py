"""Functional permissions. Institutional responsibility is checked separately."""

from fastapi import HTTPException
from app.modules.identity.rbac_service import RbacService

READ_OWN = "correspondence.read.own"
READ_UNIT = "correspondence.read.unit"
READ_ALL = "correspondence.read.all"
DERIVE = "correspondence.derive"
CONCLUDE = "correspondence.conclude"
REOPEN = "correspondence.reopen"
ATTACHMENTS_MANAGE = "correspondence.attachments.manage"
READ_PERMISSIONS = frozenset({READ_OWN, READ_UNIT, READ_ALL})
OPERATION_PERMISSIONS = frozenset({DERIVE, CONCLUDE, REOPEN, ATTACHMENTS_MANAGE})


def permissions_for(db, user):
    return frozenset(RbacService(db).get_effective_permission_codes(user.id))


def require_operation(db, user, code):
    if code not in permissions_for(db, user):
        raise HTTPException(
            status_code=403, detail="Permiso insuficiente para esta operación"
        )

"""Plan/apply a lossless legacy-role conversion; never promote to SUPERADMIN."""

import hashlib
import json
import uuid
from sqlalchemy import select, delete, Table, MetaData, text
from app.modules.identity.user import User
from app.modules.identity.role import Role
from app.modules.identity.user_role import UserRole
from app.modules.identity.user_permission import UserPermission
from app.modules.identity.permission import Permission
from app.modules.identity.rbac_service import RbacService
from app.modules.identity.administrative_continuity import (
    _lock_administration,
    protect_role_change,
)

LEGACY_ROLES = frozenset({"ADMIN", "MANAGER", "OPERATOR"})


def plan_role_transition(db, *, approved_admins=()):
    rbac = RbacService(db)
    operator = db.scalar(
        select(Role).where(Role.code == "OPERADOR", Role.is_active.is_(True))
    )
    if operator is None:
        raise ValueError("OPERADOR activo no disponible")
    plan = []
    for user in db.scalars(select(User).order_by(User.username, User.id)):
        roles = list(
            db.scalars(
                select(Role)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            )
        )
        legacy = [r for r in roles if r.code in LEGACY_ROLES]
        if not legacy:
            continue
        current_ids = sorted(str(r.id) for r in roles)
        direct_ids = set(
            db.scalars(
                select(UserPermission.permission_id).where(
                    UserPermission.user_id == user.id
                )
            )
        )
        before = set(
            rbac.get_effective_permission_codes(user.id, include_inactive=True)
        )
        needs_approval = any(
            r.code == "ADMIN" for r in legacy
        ) and user.username not in set(approved_admins)
        target_ids = {r.id for r in roles if r.code not in LEGACY_ROLES} | {operator.id}
        # Compute grants per user, not a global union attached to OPERADOR.
        inherited = set(
            rbac.get_effective_permission_codes(
                user.id,
                role_ids=target_ids,
                direct_permission_ids=[],
                include_inactive=True,
            )
        )
        direct_codes = set(
            db.scalars(select(Permission.code).where(Permission.id.in_(direct_ids)))
        )
        missing = before - inherited - direct_codes
        add_ids = set(
            db.scalars(select(Permission.id).where(Permission.code.in_(missing)))
        )
        future = set(
            rbac.get_effective_permission_codes(
                user.id,
                role_ids=target_ids,
                direct_permission_ids=direct_ids | add_ids,
                include_inactive=True,
            )
        )
        added = sorted(future - before)
        removed = sorted(before - future)
        action = (
            "SKIP_ADMIN_APPROVAL"
            if needs_approval
            else ("BLOCKED_PERMISSION_DIFF" if added or removed else "MIGRATE")
        )
        plan.append(
            dict(
                user_id=str(user.id),
                username=user.username,
                is_active=user.is_active,
                current_roles=sorted(r.code for r in roles),
                target_roles=sorted(
                    {r.code for r in roles if r.code not in LEGACY_ROLES} | {"OPERADOR"}
                ),
                current_role_ids=current_ids,
                target_role_ids=sorted(str(i) for i in target_ids),
                current_direct_permission_ids=sorted(str(i) for i in direct_ids),
                target_direct_permission_ids=sorted(
                    str(i) for i in direct_ids | add_ids
                ),
                current_permissions=sorted(before),
                future_permissions=sorted(future),
                added_permissions=added,
                removed_permissions=removed,
                action=action,
            )
        )
    return plan


def plan_sha256(plan):
    return hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def apply_role_transition(db, *, expected_sha256, approved_admins=()):
    """Caller owns a new transaction. Any failure rolls back the whole batch."""
    db.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
    _lock_administration(db)  # same lock order as ordinary administrative mutations
    db.scalars(select(User).order_by(User.id).with_for_update()).all()
    plan = plan_role_transition(db, approved_admins=approved_admins)
    if plan_sha256(plan) != expected_sha256:
        raise ValueError(
            "El plan cambió; ejecute --dry-run otra vez y revise sus diferencias"
        )
    if any(row["action"] == "BLOCKED_PERMISSION_DIFF" for row in plan):
        raise ValueError(
            "El rol objetivo agregaría o retiraría capacidades; no se aplicó el lote"
        )
    audit = Table(
        "authorization_migration_audits", MetaData(), autoload_with=db.connection()
    )
    batch_id = uuid.uuid4()
    for row in plan:
        if row["action"] != "MIGRATE":
            continue
        uid = uuid.UUID(row["user_id"])
        user = db.get(User, uid)
        target_roles = [uuid.UUID(i) for i in row["target_role_ids"]]
        target_direct = [uuid.UUID(i) for i in row["target_direct_permission_ids"]]
        protect_role_change(
            db,
            user,
            target_roles,
            user.employee_id,
            direct_permission_ids=target_direct,
        )
        old_roles = set(uuid.UUID(i) for i in row["current_role_ids"])
        db.execute(
            delete(UserRole).where(
                UserRole.user_id == uid,
                UserRole.role_id.in_(old_roles - set(target_roles)),
            )
        )
        for rid in set(target_roles) - old_roles:
            db.add(UserRole(user_id=uid, role_id=rid))
        old_direct = set(uuid.UUID(i) for i in row["current_direct_permission_ids"])
        for pid in set(target_direct) - old_direct:
            db.add(UserPermission(user_id=uid, permission_id=pid))
        db.flush()
        actual = RbacService(db).get_effective_permission_codes(
            uid, include_inactive=True
        )
        if actual != row["current_permissions"]:
            raise ValueError(
                "La equivalencia de permisos no se conserva para " + row["username"]
            )
        db.execute(
            audit.insert().values(
                id=uuid.uuid4(),
                batch_id=batch_id,
                user_id=uid,
                before_state={
                    "roles": row["current_roles"],
                    "role_ids": row["current_role_ids"],
                    "direct_permission_ids": row["current_direct_permission_ids"],
                    "permissions": row["current_permissions"],
                },
                after_state={
                    "roles": row["target_roles"],
                    "role_ids": row["target_role_ids"],
                    "direct_permission_ids": row["target_direct_permission_ids"],
                    "permissions": actual,
                },
            )
        )
    return {
        "batch_id": str(batch_id),
        "migrated": sum(row["action"] == "MIGRATE" for row in plan),
        "skipped_admin": sum(row["action"] == "SKIP_ADMIN_APPROVAL" for row in plan),
    }

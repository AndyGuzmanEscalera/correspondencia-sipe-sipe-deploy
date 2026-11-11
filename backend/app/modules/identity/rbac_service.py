import uuid
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.identity.permission import Permission
from app.modules.identity.role import Role
from app.modules.identity.role_permission import RolePermission
from app.modules.identity.user import User
from app.modules.identity.user_permission import UserPermission
from app.modules.identity.user_role import UserRole


class RbacService:
    """Single source of functional capabilities, never institutional context."""

    SUPERADMIN = "SUPERADMIN"

    def __init__(self, db: Session) -> None:
        self._db = db

    def _active_roles(self, user_id, role_ids=None):
        query = select(Role).where(Role.is_active.is_(True))
        if role_ids is None:
            query = query.join(UserRole, UserRole.role_id == Role.id).where(
                UserRole.user_id == user_id
            )
        else:
            query = query.where(Role.id.in_(role_ids))
        return list(self._db.scalars(query))

    def get_user_role_codes(self, user_id: uuid.UUID) -> list[str]:
        return sorted(role.code for role in self._active_roles(user_id))

    def is_superadmin(self, user_id: uuid.UUID) -> bool:
        user = self._db.get(User, user_id)
        return bool(
            user
            and user.is_active
            and any(
                role.code == self.SUPERADMIN for role in self._active_roles(user_id)
            )
        )

    def get_direct_permission_codes(self, user_id: uuid.UUID) -> list[str]:
        return list(
            self._db.scalars(
                select(Permission.code)
                .join(UserPermission, UserPermission.permission_id == Permission.id)
                .where(UserPermission.user_id == user_id)
                .order_by(Permission.code)
            )
        )

    def get_effective_permission_codes(
        self,
        user_id: uuid.UUID,
        *,
        role_ids=None,
        direct_permission_ids=None,
        include_inactive=False
    ) -> list[str]:
        # Overrides evaluate proposed changes without persisting them.
        user = self._db.get(User, user_id)
        if user is None or (not user.is_active and not include_inactive):
            return []
        roles = self._active_roles(user_id, role_ids)
        if any(role.code == self.SUPERADMIN for role in roles):
            return list(
                self._db.scalars(select(Permission.code).order_by(Permission.code))
            )
        inherited = set(
            self._db.scalars(
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .where(RolePermission.role_id.in_([r.id for r in roles]))
            )
        )
        direct = (
            self.get_direct_permission_codes(user_id)
            if direct_permission_ids is None
            else self._db.scalars(
                select(Permission.code).where(Permission.id.in_(direct_permission_ids))
            )
        )
        return sorted(inherited | set(direct))

    def get_user_permission_codes(self, user_id: uuid.UUID) -> list[str]:
        return self.get_effective_permission_codes(user_id)

    def has_permission(self, user_id: uuid.UUID, permission_code: str) -> bool:
        return permission_code in self.get_effective_permission_codes(user_id)

    def validate_active_role_ids(self, role_ids: list[uuid.UUID]) -> None:
        found = set(
            self._db.scalars(
                select(Role.id).where(Role.id.in_(role_ids), Role.is_active.is_(True))
            )
        )
        if not role_ids or found != set(role_ids):
            raise HTTPException(
                status_code=422, detail="Uno o más roles no existen o están inactivos"
            )

    def assert_can_assign_roles(
        self, actor, role_ids, *, target_id=None, current_role_ids=()
    ):
        if not self.has_permission(actor.id, "master_data.roles.assign"):
            raise HTTPException(
                status_code=403, detail="No tienes permisos para asignar roles."
            )
        for role in self._active_roles(actor.id, set(role_ids) ^ set(current_role_ids)):
            if role.code == self.SUPERADMIN:
                if target_id == actor.id or not self.is_superadmin(actor.id):
                    raise HTTPException(
                        status_code=403,
                        detail="Solo SUPERADMIN puede asignar este rol a otra cuenta.",
                    )
            elif role.id in set(role_ids) and not self.is_superadmin(actor.id):
                codes = set(
                    self._db.scalars(
                        select(Permission.code)
                        .join(
                            RolePermission,
                            RolePermission.permission_id == Permission.id,
                        )
                        .where(RolePermission.role_id == role.id)
                    )
                )
                if not codes <= set(self.get_effective_permission_codes(actor.id)):
                    raise HTTPException(
                        status_code=403,
                        detail="No puede conceder capacidades que no posee.",
                    )

    def assert_can_manage_permissions(self, actor, permission_ids, *, target_id=None):
        if not all(
            self.has_permission(actor.id, c)
            for c in ("master_data.users.manage", "master_data.roles.assign")
        ):
            raise HTTPException(
                status_code=403,
                detail="Permiso insuficiente para administrar permisos directos",
            )
        if target_id == actor.id and not self.is_superadmin(actor.id):
            existing = set(
                self._db.scalars(
                    select(UserPermission.permission_id).where(
                        UserPermission.user_id == actor.id
                    )
                )
            )
            added = set(permission_ids) - existing
            if self._db.scalar(
                select(Permission.id)
                .where(
                    Permission.id.in_(added),
                    (
                        Permission.code.startswith("master_data.")
                        | Permission.code.startswith("system.settings.")
                    ),
                )
                .limit(1)
            ):
                raise HTTPException(
                    status_code=403,
                    detail="No puede asignarse permisos administrativos directos",
                )
        rows = list(
            self._db.scalars(
                select(Permission).where(Permission.id.in_(permission_ids))
            )
        )
        if {p.id for p in rows} != set(permission_ids):
            raise HTTPException(status_code=422, detail="Uno o más permisos no existen")
        if not {p.code for p in rows} <= set(
            self.get_effective_permission_codes(actor.id)
        ):
            raise HTTPException(
                status_code=403, detail="No puede conceder capacidades que no posee"
            )

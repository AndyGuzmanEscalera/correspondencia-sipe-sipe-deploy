"""Add role assignment capability; preserve existing RBAC grants.

Revision ID: c9d0e1f2a3b4
Revises: a7c8d9e0f1a2
"""
import uuid

from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "a7c8d9e0f1a2"
branch_labels = None
depends_on = None

PERMISSION_CODE = "master_data.roles.assign"


def upgrade() -> None:
    bind = op.get_bind()
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()),
                           sa.column("code", sa.String()),
                           sa.column("description", sa.String()))
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    grants = sa.table("role_permissions", sa.column("role_id", sa.Uuid()),
                      sa.column("permission_id", sa.Uuid()))
    permission_id = bind.scalar(sa.select(permissions.c.id).where(
        permissions.c.code == PERMISSION_CODE))
    if permission_id is None:
        permission_id = uuid.uuid4()
        bind.execute(permissions.insert().values(
            id=permission_id, code=PERMISSION_CODE, description="Asignar roles a usuarios"))
    # Base seed only: runtime authorization always checks permissions.
    admin_id = bind.scalar(sa.select(roles.c.id).where(roles.c.code == "ADMIN"))
    if admin_id is not None:
        existing = bind.scalar(sa.select(grants.c.role_id).where(
            grants.c.role_id == admin_id, grants.c.permission_id == permission_id))
        if existing is None:
            bind.execute(grants.insert().values(role_id=admin_id, permission_id=permission_id))


def downgrade() -> None:
    # Add-only: retain the permission and any subsequent custom grants.
    pass

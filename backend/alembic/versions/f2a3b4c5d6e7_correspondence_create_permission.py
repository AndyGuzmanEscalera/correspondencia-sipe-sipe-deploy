"""Permiso dinámico de creación; conserva los permisos personalizados."""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()),
        sa.column("code", sa.String()), sa.column("description", sa.String()))
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    grants = sa.table("role_permissions", sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()))
    pid = bind.scalar(sa.select(permissions.c.id).where(permissions.c.code == "correspondence.create"))
    if pid is None:
        pid = uuid.uuid4()
        bind.execute(permissions.insert().values(id=pid, code="correspondence.create", description="Crear hojas de ruta"))
    # Compatibilidad de roles base únicamente en seed; runtime usa permissions.
    for rid in bind.scalars(sa.select(roles.c.id).where(roles.c.code.in_(["ADMIN", "MANAGER", "OPERATOR"]))):
        if bind.scalar(sa.select(grants.c.role_id).where(grants.c.role_id == rid, grants.c.permission_id == pid)) is None:
            bind.execute(grants.insert().values(role_id=rid, permission_id=pid))


def downgrade():
    # Aditiva: no eliminar grants, incluidos los personalizados.
    pass

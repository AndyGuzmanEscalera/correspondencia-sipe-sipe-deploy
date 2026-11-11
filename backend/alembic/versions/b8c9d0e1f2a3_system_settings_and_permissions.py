"""System settings and additive ADMIN configuration grants.

Revision ID: b8c9d0e1f2a3
Revises: c9d0e1f2a3b4
"""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None

NEW_PERMISSIONS = [("system.settings.read", "Ver configuración del sistema"),
                   ("system.settings.manage", "Administrar configuración del sistema")]


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("system_settings"):
        op.create_table("system_settings",
            sa.Column("key", sa.String(100), primary_key=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True),
                      server_default=sa.func.now(), nullable=False),
            sa.Column("updated_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")))
    permissions = sa.table("permissions", sa.column("id", sa.Uuid()),
                          sa.column("code", sa.String()), sa.column("description", sa.String()))
    roles = sa.table("roles", sa.column("id", sa.Uuid()), sa.column("code", sa.String()))
    grants = sa.table("role_permissions", sa.column("role_id", sa.Uuid()),
                      sa.column("permission_id", sa.Uuid()))
    admin_id = bind.scalar(sa.select(roles.c.id).where(roles.c.code == "ADMIN"))
    for code, description in NEW_PERMISSIONS:
        permission_id = bind.scalar(sa.select(permissions.c.id).where(permissions.c.code == code))
        if permission_id is None:
            permission_id = uuid.uuid4()
            bind.execute(permissions.insert().values(id=permission_id, code=code, description=description))
        if admin_id is not None and bind.scalar(sa.select(grants.c.role_id).where(
                grants.c.role_id == admin_id, grants.c.permission_id == permission_id)) is None:
            bind.execute(grants.insert().values(role_id=admin_id, permission_id=permission_id))


def downgrade():
    # Preserve settings, permissions, and grants created or extended by users.
    pass

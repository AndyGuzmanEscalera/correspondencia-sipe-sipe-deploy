"""Additive authorization and institutional hierarchy foundation."""

import uuid
from alembic import op
import sqlalchemy as sa

revision = "i1b2c3d4e5f6"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def seed_base_data(bind):
    for rank, code in enumerate(
        ("FUNCIONARIO", "RESPONSABLE", "JEFE", "DIRECTOR", "SECRETARIO", "ALCALDE"), 1
    ):
        bind.execute(
            sa.text(
                """INSERT INTO position_hierarchies (id,code,name,rank,is_active)
            VALUES (:id,:code,:name,:rank,TRUE) ON CONFLICT DO NOTHING"""
            ),
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "gam-sipe-sipe/hierarchy/" + code),
                "code": code,
                "name": code.capitalize(),
                "rank": rank,
            },
        )
    for code, name in (("SUPERADMIN", "Superadministrador"), ("OPERADOR", "Operador")):
        bind.execute(
            sa.text(
                """INSERT INTO roles (id,code,name,is_active)
            VALUES (:id,:code,:name,TRUE) ON CONFLICT (code) DO NOTHING"""
            ),
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, "gam-sipe-sipe/role/" + code),
                "code": code,
                "name": name,
            },
        )


def upgrade():
    op.create_table(
        "position_hierarchies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("rank > 0", name="ck_position_hierarchies_rank_positive"),
    )
    op.create_index(
        "uq_position_hierarchies_code_lower",
        "position_hierarchies",
        [sa.text("LOWER(code)")],
        unique=True,
    )
    op.add_column("positions", sa.Column("hierarchy_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_positions_hierarchy_id",
        "positions",
        "position_hierarchies",
        ["hierarchy_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "user_permissions",
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "permission_id",
            sa.Uuid(),
            sa.ForeignKey("permissions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "granted_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    seed_base_data(op.get_bind())


def downgrade():
    # This migration carries grants and institutional references. Require an
    # explicit reconciliation plan rather than silently destroying them.
    raise RuntimeError(
        "Downgrade destructivo no soportado; preserve jerarquías y permisos."
    )

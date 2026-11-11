"""Explicit operational permissions and controlled legacy transition audit."""

import uuid
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "k1b2c3d4e5f6"
down_revision = "j1b2c3d4e5f6"
branch_labels = None
depends_on = None
PERMISSIONS = {
    "correspondence.read.own": "Leer correspondencia propia o relacionada",
    "correspondence.read.unit": "Leer correspondencia de la unidad",
    "correspondence.read.all": "Leer correspondencia institucional global",
    "correspondence.derive": "Derivar correspondencia",
    "correspondence.conclude": "Concluir correspondencia",
    "correspondence.reopen": "Reabrir correspondencia",
    "correspondence.attachments.manage": "Gestionar adjuntos de correspondencia",
}
BASELINE = set(PERMISSIONS) - {"correspondence.read.unit", "correspondence.read.all"}


def seed_permissions(db):
    for code, description in PERMISSIONS.items():
        db.execute(
            sa.text(
                """INSERT INTO permissions (id,code,description)
            VALUES (:id,:code,:description) ON CONFLICT (code) DO NOTHING"""
            ),
            dict(
                id=uuid.uuid5(uuid.NAMESPACE_URL, code),
                code=code,
                description=description,
            ),
        )
    # These operations were implicit for existing accounts. Preserve them once,
    # including inactive accounts, whose activation/context checks remain mandatory.
    for code in BASELINE:
        db.execute(
            sa.text(
                """INSERT INTO user_permissions (user_id,permission_id)
            SELECT u.id,p.id FROM users u CROSS JOIN permissions p WHERE p.code=:code
            ON CONFLICT DO NOTHING"""
            ),
            dict(code=code),
        )
        # Legacy provisioning remains usable until the controlled role conversion.
        # OPERADOR stays an empty base role: no union of privileged legacy grants.
        db.execute(
            sa.text(
                """INSERT INTO role_permissions (role_id,permission_id)
            SELECT r.id,p.id FROM roles r CROSS JOIN permissions p
            WHERE r.code IN ('ADMIN','MANAGER','OPERATOR') AND p.code=:code
            ON CONFLICT DO NOTHING"""
            ),
            dict(code=code),
        )
    db.execute(
        sa.text(
            """INSERT INTO user_permissions (user_id,permission_id)
        SELECT u.id,p.id FROM users u JOIN employees e ON e.id=u.employee_id
        JOIN positions c ON c.id=e.position_id CROSS JOIN permissions p
        WHERE c.can_view_unit_correspondence=TRUE AND p.code='correspondence.read.unit'
        ON CONFLICT DO NOTHING"""
        )
    )


def upgrade():
    op.create_table(
        "authorization_migration_audits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("before_state", JSONB(), nullable=False),
        sa.Column("after_state", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "batch_id", "user_id", name="uq_authorization_migration_batch_user"
        ),
    )
    seed_permissions(op.get_bind())


def downgrade():
    raise RuntimeError(
        "Se requiere un plan explícito para preservar concesiones y auditoría."
    )

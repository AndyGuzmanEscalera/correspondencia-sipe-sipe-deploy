"""Operational document profiles and actor-based creation capabilities."""

import uuid
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "j1b2c3d4e5f6"
down_revision = "i1b2c3d4e5f6"
branch_labels = None
depends_on = None

SPECS = (
    (
        "COMUNICADO_INTERNO",
        "Comunicado interno",
        "INTERNAL_COMMUNICATION",
        "internal_communication",
        None,
    ),
    ("INFORME", "Informe", "REPORT", "report", None),
    ("MEMORANDUM", "Memorándum", "MEMORANDUM", "memorandum", "JEFE"),
    ("CIRCULAR", "Circular", "CIRCULAR", "circular", "DIRECTOR"),
)


def seed_operational_types(db):
    legacy_permission = db.scalar(
        sa.text("SELECT id FROM permissions WHERE code='correspondence.create'")
    )
    for code, name, profile, suffix, minimum_code in SPECS:
        rows = db.execute(
            sa.text("SELECT id FROM document_types WHERE UPPER(TRIM(code))=:code"),
            {"code": code},
        ).all()
        if len(rows) > 1:
            raise RuntimeError("Catálogo ambiguo para " + code)
        id = (
            rows[0][0]
            if rows
            else uuid.uuid5(uuid.NAMESPACE_URL, "gam-sipe-sipe/document/" + code)
        )
        minimum = (
            db.scalar(
                sa.text("SELECT id FROM position_hierarchies WHERE code=:code"),
                {"code": minimum_code},
            )
            if minimum_code
            else None
        )
        if minimum_code and minimum is None:
            raise RuntimeError("Falta jerarquía estructural " + minimum_code)
        permission_code = "correspondence.create." + suffix
        db.execute(
            sa.text(
                """INSERT INTO permissions (id,code,description) VALUES (:id,:code,:description)
            ON CONFLICT (code) DO NOTHING"""
            ),
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, permission_code),
                "code": permission_code,
                "description": "Crear " + name,
            },
        )
        permission = db.scalar(
            sa.text("SELECT id FROM permissions WHERE code=:code"),
            {"code": permission_code},
        )
        if not rows:
            db.execute(
                sa.text(
                    """INSERT INTO document_types (id,code,name,is_active)
                VALUES (:id,:code,:name,TRUE)"""
                ),
                {"id": id, "code": code, "name": name},
            )
        # Reuse IDs, display names and historical references. Structural fields
        # are protected by the administrative API.
        db.execute(
            sa.text(
                """UPDATE document_types SET profile=:profile,is_operational=TRUE,
            creation_permission=:permission,minimum_hierarchy_id=:minimum,is_active=TRUE WHERE id=:id"""
            ),
            {
                "profile": profile,
                "permission": permission_code,
                "minimum": minimum,
                "id": id,
            },
        )
        if legacy_permission:
            # Additive transition: preserve existing functional capacity without
            # granting the legacy permission a runtime hierarchy bypass.
            db.execute(
                sa.text(
                    """INSERT INTO role_permissions (role_id,permission_id)
                SELECT role_id,:permission FROM role_permissions WHERE permission_id=:legacy
                ON CONFLICT DO NOTHING"""
                ),
                {"permission": permission, "legacy": legacy_permission},
            )
            db.execute(
                sa.text(
                    """INSERT INTO user_permissions (user_id,permission_id,granted_by_user_id)
                SELECT user_id,:permission,granted_by_user_id FROM user_permissions WHERE permission_id=:legacy
                ON CONFLICT DO NOTHING"""
                ),
                {"permission": permission, "legacy": legacy_permission},
            )
    db.execute(
        sa.text(
            """UPDATE document_types SET is_active=FALSE,is_operational=FALSE
        WHERE UPPER(TRIM(code)) NOT IN ('COMUNICADO_INTERNO','INFORME','MEMORANDUM','CIRCULAR')"""
        )
    )
    db.execute(
        sa.text(
            """UPDATE document_types SET profile='LEGACY_CHAINING'
        WHERE UPPER(TRIM(code)) IN ('EDIE','S','ENCADENAMIENTO DE DOCUMENTOS INTERNOS Y EXTERNOS')"""
        )
    )
    db.execute(
        sa.text(
            "UPDATE document_types SET profile='LEGACY_INTERNAL_NOTE' WHERE UPPER(TRIM(code))='NOTA'"
        )
    )


def upgrade():
    op.add_column(
        "document_types",
        sa.Column("profile", sa.String(40), nullable=False, server_default="GENERIC"),
    )
    op.add_column(
        "document_types",
        sa.Column(
            "is_operational", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "document_types",
        sa.Column("creation_permission", sa.String(100), nullable=True),
    )
    op.add_column(
        "document_types", sa.Column("minimum_hierarchy_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_document_types_minimum_hierarchy",
        "document_types",
        "position_hierarchies",
        ["minimum_hierarchy_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "correspondences",
        sa.Column("document_snapshot", JSONB(none_as_null=True), nullable=True),
    )
    seed_operational_types(op.get_bind())


def downgrade():
    raise RuntimeError(
        "Requiere un plan explícito que preserve perfiles, permisos y snapshots históricos."
    )

"""Stable document behavior and explicit route sheet issuance data.

Reuses the persisted profile column; profile remains a compatibility API alias.
No historical correspondence IDs, snapshots, dates or content are rewritten.
"""

import uuid
from alembic import op
import sqlalchemy as sa

revision = "l1b2c3d4e5f6"
down_revision = "k1b2c3d4e5f6"
branch_labels = None
depends_on = None

BEHAVIORS = {
    "HOJA_RUTA": "ROUTE_SHEET",
    "COMUNICADO_INTERNO": "INTERNAL_COMMUNICATION",
    "INFORME": "REPORT",
    "MEMORANDUM": "MEMORANDUM",
    "CIRCULAR": "CIRCULAR",
    "EDIE": "LEGACY_CHAINING",
    "S": "LEGACY_CHAINING",
    "ENCADENAMIENTO DE DOCUMENTOS INTERNOS Y EXTERNOS": "LEGACY_CHAINING",
    "NOTA": "LEGACY_INTERNAL_NOTE",
}


def upgrade():
    db = op.get_bind()
    for code, behavior in BEHAVIORS.items():
        db.execute(
            sa.text(
                "UPDATE document_types SET profile=:behavior WHERE UPPER(TRIM(code))=:code"
            ),
            dict(code=code, behavior=behavior),
        )
    db.execute(
        sa.text(
            """INSERT INTO document_types
        (id,code,name,profile,is_active,is_operational,creation_permission)
        VALUES (:id,'HOJA_RUTA','Hoja de Ruta','ROUTE_SHEET',TRUE,TRUE,'correspondence.create')
        ON CONFLICT DO NOTHING"""
        ),
        {"id": uuid.uuid5(uuid.NAMESPACE_URL, "gam-sipe-sipe/document/HOJA_RUTA")},
    )
    # An existing HOJA_RUTA retains its identity/name and adopts the same
    # structural creation permission; no permission is granted to any actor.
    db.execute(
        sa.text(
            """UPDATE document_types SET profile='ROUTE_SHEET',
        is_operational=TRUE,creation_permission='correspondence.create',
        minimum_hierarchy_id=NULL WHERE UPPER(TRIM(code))='HOJA_RUTA'"""
        )
    )
    op.create_check_constraint(
        "ck_document_types_behavior",
        "document_types",
        "profile IN ('ROUTE_SHEET','INTERNAL_COMMUNICATION','REPORT','MEMORANDUM','CIRCULAR','GENERIC','LEGACY_CHAINING','LEGACY_INTERNAL_NOTE')",
    )
    op.add_column(
        "correspondences", sa.Column("emission_date", sa.Date(), nullable=True)
    )
    op.add_column(
        "correspondences", sa.Column("emission_time", sa.Time(), nullable=True)
    )
    op.add_column(
        "correspondences", sa.Column("page_count", sa.Integer(), nullable=True)
    )
    op.create_check_constraint(
        "ck_correspondences_page_count",
        "correspondences",
        "page_count IS NULL OR page_count > 0",
    )


def downgrade():
    raise RuntimeError(
        "Requires an explicit plan preserving issued route sheets and snapshots."
    )

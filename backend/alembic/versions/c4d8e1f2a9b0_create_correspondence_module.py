"""create correspondence module

Revision ID: c4d8e1f2a9b0
Revises: ba5e8722e8d4
Create Date: 2026-09-22 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4d8e1f2a9b0"
down_revision: Union[str, None] = "ba5e8722e8d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DOCUMENT_TYPES = [
    ("a1000001-0001-4001-8001-000000000001", "CARTA", "Carta"),
    ("a1000001-0001-4001-8001-000000000002", "INFORME", "Informe"),
    ("a1000001-0001-4001-8001-000000000003", "MEMORANDUM", "Memorándum"),
    ("a1000001-0001-4001-8001-000000000004", "CIRCULAR", "Circular"),
    ("a1000001-0001-4001-8001-000000000005", "SOLICITUD", "Solicitud"),
    ("a1000001-0001-4001-8001-000000000006", "NOTA", "Nota"),
    ("a1000001-0001-4001-8001-000000000007", "CERTIFICACION", "Certificación"),
]


def upgrade() -> None:
    op.create_table(
        "document_types",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    document_types = sa.table(
        "document_types",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
    )
    op.bulk_insert(
        document_types,
        [
            {
                "id": row[0],
                "code": row[1],
                "name": row[2],
                "is_active": True,
            }
            for row in DOCUMENT_TYPES
        ],
    )

    op.create_table(
        "correspondence_route_sequences",
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("year"),
    )

    op.create_table(
        "correspondences",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("route_number", sa.String(length=30), nullable=False),
        sa.Column("route_sequence", sa.Integer(), nullable=False),
        sa.Column("route_year", sa.Integer(), nullable=False),
        sa.Column("correspondence_type", sa.String(length=20), nullable=False),
        sa.Column("document_type_id", sa.UUID(), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("reference", sa.String(length=200), nullable=True),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("sender_name", sa.String(length=200), nullable=True),
        sa.Column("sender_document", sa.String(length=30), nullable=True),
        sa.Column("sender_contact", sa.String(length=50), nullable=True),
        sa.Column("origin_description", sa.String(length=300), nullable=True),
        sa.Column("origin_unit_id", sa.UUID(), nullable=True),
        sa.Column("origin_user_id", sa.UUID(), nullable=True),
        sa.Column("current_unit_id", sa.UUID(), nullable=True),
        sa.Column("current_user_id", sa.UUID(), nullable=True),
        sa.Column("cite", sa.String(length=100), nullable=True),
        sa.Column("cite_sequence", sa.Integer(), nullable=True),
        sa.Column("cite_year", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("deletion_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("concluded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_unit_id"], ["organizational_units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["current_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["document_type_id"], ["document_types.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["origin_unit_id"], ["organizational_units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["origin_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_number", name="uq_correspondences_route_number"),
        sa.UniqueConstraint(
            "route_year",
            "route_sequence",
            name="uq_correspondences_route_year_sequence",
        ),
    )
    op.create_index("ix_correspondences_is_active", "correspondences", ["is_active"])
    op.create_index(
        "ix_correspondences_is_active_status",
        "correspondences",
        ["is_active", "status"],
    )

    op.create_table(
        "correspondence_movements",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("correspondence_id", sa.UUID(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("movement_type", sa.String(length=30), nullable=False),
        sa.Column("from_unit_id", sa.UUID(), nullable=True),
        sa.Column("from_user_id", sa.UUID(), nullable=True),
        sa.Column("to_unit_id", sa.UUID(), nullable=True),
        sa.Column("to_user_id", sa.UUID(), nullable=True),
        sa.Column("instruction", sa.String(length=500), nullable=True),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.UUID(), nullable=True),
        sa.Column("cancellation_reason", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["correspondence_id"],
            ["correspondences.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_unit_id"], ["organizational_units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["from_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_unit_id"], ["organizational_units.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["to_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "correspondence_id",
            "sequence_number",
            name="uq_correspondence_movements_correspondence_sequence",
        ),
    )

    op.create_table(
        "correspondence_attachments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("correspondence_id", sa.UUID(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("storage_path", sa.String(length=500), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_by_user_id", sa.UUID(), nullable=True),
        sa.Column("deletion_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["correspondence_id"],
            ["correspondences.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["deleted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("correspondence_attachments")
    op.drop_table("correspondence_movements")
    op.drop_index("ix_correspondences_is_active_status", table_name="correspondences")
    op.drop_index("ix_correspondences_is_active", table_name="correspondences")
    op.drop_table("correspondences")
    op.drop_table("correspondence_route_sequences")
    op.drop_table("document_types")

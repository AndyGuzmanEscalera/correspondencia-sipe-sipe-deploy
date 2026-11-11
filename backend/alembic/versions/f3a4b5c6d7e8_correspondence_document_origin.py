"""correspondence document origin, description and per-type numbering

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-24 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "correspondence_document_sequences",
        sa.Column("document_type_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["document_types.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("document_type_id", "year"),
    )

    op.add_column(
        "correspondences",
        sa.Column("origin_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "correspondences",
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.add_column(
        "correspondences",
        sa.Column("document_sequence", sa.Integer(), nullable=True),
    )
    op.add_column(
        "correspondences",
        sa.Column("document_year", sa.Integer(), nullable=True),
    )
    op.add_column(
        "correspondences",
        sa.Column("document_number", sa.String(length=30), nullable=True),
    )
    op.create_foreign_key(
        "fk_correspondences_origin_employee_id",
        "correspondences",
        "employees",
        ["origin_employee_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_correspondences_document_type_year_sequence",
        "correspondences",
        ["document_type_id", "document_year", "document_sequence"],
    )

    # Align seeded catalog names for operational document types (no new codes).
    op.execute(
        """
        UPDATE document_types
        SET name = 'Informe Técnico', is_active = TRUE
        WHERE code = 'INFORME'
        """
    )
    op.execute(
        """
        UPDATE document_types
        SET name = 'Nota Interna', is_active = TRUE
        WHERE code = 'NOTA'
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_correspondences_document_type_year_sequence",
        "correspondences",
        type_="unique",
    )
    op.drop_constraint(
        "fk_correspondences_origin_employee_id",
        "correspondences",
        type_="foreignkey",
    )
    op.drop_column("correspondences", "document_number")
    op.drop_column("correspondences", "document_year")
    op.drop_column("correspondences", "document_sequence")
    op.drop_column("correspondences", "description")
    op.drop_column("correspondences", "origin_employee_id")
    op.drop_table("correspondence_document_sequences")

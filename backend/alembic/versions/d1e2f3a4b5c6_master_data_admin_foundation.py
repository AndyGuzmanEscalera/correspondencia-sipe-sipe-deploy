"""master data admin foundation

Revision ID: d1e2f3a4b5c6
Revises: c4d8e1f2a9b0
Create Date: 2026-09-22 22:00:00.000000

Adds audit columns, catalog fields, case-insensitive unique indexes,
document_types.updated_at, and master_data.* permission seeds.

Legacy rows may keep nullable code/document_number at DB level;
admin API enforces required values for new records.
"""

from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, None] = "c4d8e1f2a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS: list[tuple[str, str]] = [
    ("master_data.organizational_units.read", "Listar unidades (administración)"),
    ("master_data.organizational_units.manage", "Administrar unidades organizacionales"),
    ("master_data.positions.read", "Listar cargos (administración)"),
    ("master_data.positions.manage", "Administrar cargos"),
    ("master_data.employees.read", "Listar funcionarios (administración)"),
    ("master_data.employees.manage", "Administrar funcionarios"),
    ("master_data.users.read", "Listar usuarios (administración)"),
    ("master_data.users.manage", "Administrar usuarios"),
    ("master_data.document_types.read", "Listar tipos de documento (administración)"),
    ("master_data.document_types.manage", "Administrar tipos de documento"),
]


def _audit_columns() -> list[sa.Column]:
    return [
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("updated_by_user_id", sa.UUID(), nullable=True),
    ]


def upgrade() -> None:
    for table in (
        "organizational_units",
        "positions",
        "employees",
        "users",
        "document_types",
    ):
        op.add_column(table, sa.Column("created_by_user_id", sa.UUID(), nullable=True))
        op.add_column(table, sa.Column("updated_by_user_id", sa.UUID(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_created_by_user_id",
            table,
            "users",
            ["created_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_foreign_key(
            f"fk_{table}_updated_by_user_id",
            table,
            "users",
            ["updated_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "organizational_units",
        sa.Column("description", sa.String(length=300), nullable=True),
    )

    op.add_column("positions", sa.Column("code", sa.String(length=50), nullable=True))
    op.add_column(
        "positions",
        sa.Column("description", sa.String(length=255), nullable=True),
    )

    op.add_column(
        "document_types",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    # Legacy duplicates: keep earliest row code, nullify others (admin must normalize).
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY LOWER(code)
                       ORDER BY created_at, id
                   ) AS rn
            FROM organizational_units
            WHERE code IS NOT NULL
        )
        UPDATE organizational_units AS u
        SET code = NULL
        FROM ranked AS r
        WHERE u.id = r.id AND r.rn > 1
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_organizational_units_code_lower
        ON organizational_units (LOWER(code))
        WHERE code IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_positions_code_lower
        ON positions (LOWER(code))
        WHERE code IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_employees_document_number_lower
        ON employees (LOWER(document_number))
        WHERE document_number IS NOT NULL
        """
    )

    permissions_table = sa.table(
        "permissions",
        sa.column("id", sa.UUID()),
        sa.column("code", sa.String()),
        sa.column("description", sa.String()),
    )
    op.bulk_insert(
        permissions_table,
        [
            {
                "id": uuid.uuid4(),
                "code": code,
                "description": description,
            }
            for code, description in PERMISSIONS
        ],
    )


def downgrade() -> None:
    for code, _ in reversed(PERMISSIONS):
        op.execute(
            sa.text("DELETE FROM permissions WHERE code = :code").bindparams(code=code)
        )

    op.drop_index("uq_employees_document_number_lower", table_name="employees")
    op.drop_index("uq_positions_code_lower", table_name="positions")
    op.drop_index("uq_organizational_units_code_lower", table_name="organizational_units")

    op.drop_column("document_types", "updated_at")
    op.drop_column("positions", "description")
    op.drop_column("positions", "code")
    op.drop_column("organizational_units", "description")

    for table in reversed(
        ("document_types", "users", "employees", "positions", "organizational_units")
    ):
        op.drop_constraint(f"fk_{table}_updated_by_user_id", table, type_="foreignkey")
        op.drop_constraint(f"fk_{table}_created_by_user_id", table, type_="foreignkey")
        op.drop_column(table, "updated_by_user_id")
        op.drop_column(table, "created_by_user_id")

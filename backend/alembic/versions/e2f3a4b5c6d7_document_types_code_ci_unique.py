"""document types code case-insensitive unique

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-22 23:30:00.000000

Replaces case-sensitive document_types.code unique constraint with a
case-insensitive unique index (LOWER(code)).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("document_types_code_key", "document_types", type_="unique")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_document_types_code_lower
        ON document_types (LOWER(code))
        """
    )


def downgrade() -> None:
    op.drop_index("uq_document_types_code_lower", table_name="document_types")
    op.create_unique_constraint("document_types_code_key", "document_types", ["code"])

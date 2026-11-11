"""institutional base roles seed (ADMIN, MANAGER, OPERATOR)

Revision ID: a7c8d9e0f1a2
Revises: f3a4b5c6d7e8
Create Date: 2026-09-28 00:00:00.000000

Idempotent data migration: upserts three base roles and adds missing
master_data role_permissions. Does not delete roles, permissions, or grants.
"""

from typing import Sequence, Union

from alembic import op

from app.modules.identity.institutional_base_roles import ensure_institutional_base_roles

revision: str = "a7c8d9e0f1a2"
down_revision: Union[str, None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    ensure_institutional_base_roles(connection)


def downgrade() -> None:
    # Conservative: base roles may already be assigned to users and extended
    # with future permissions (audit.*, reports.*, etc.). No destructive rollback.
    pass

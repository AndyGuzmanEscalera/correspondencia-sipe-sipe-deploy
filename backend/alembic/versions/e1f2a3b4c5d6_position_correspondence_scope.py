"""Alcance de lectura de correspondencia explícito por cargo."""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade():
    if "can_view_unit_correspondence" not in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("positions")
    }:
        op.add_column("positions", sa.Column(
            "can_view_unit_correspondence", sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))


def downgrade():
    # Conservar la configuración de alcance y las referencias históricas.
    pass

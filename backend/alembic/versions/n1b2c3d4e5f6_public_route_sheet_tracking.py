"""Private document verifier for opt-in public route sheet tracking."""
from alembic import op
import sqlalchemy as sa

revision = "n1b2c3d4e5f6"
down_revision = "m1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("correspondences", sa.Column("origin_external_document_hash", sa.String(64), nullable=True))
    op.add_column("correspondences", sa.Column("origin_external_document_last4", sa.String(4), nullable=True))


def downgrade():
    op.drop_column("correspondences", "origin_external_document_last4")
    op.drop_column("correspondences", "origin_external_document_hash")

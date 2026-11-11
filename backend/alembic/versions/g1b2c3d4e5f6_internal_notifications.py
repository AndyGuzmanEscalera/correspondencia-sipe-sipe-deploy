"""Internal notifications linked atomically to correspondence movements."""
from alembic import op
import sqlalchemy as sa
revision = "g1b2c3d4e5f6"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("correspondence_id", sa.Uuid(), sa.ForeignKey("correspondences.id", ondelete="SET NULL")),
        sa.Column("movement_id", sa.Uuid(), sa.ForeignKey("correspondence_movements.id", ondelete="CASCADE")),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("movement_id", "user_id", name="uq_notifications_movement_user"))
    op.create_index("ix_notifications_user_read_created", "notifications", ["user_id", "is_read", "created_at"])

def downgrade():
    op.drop_table("notifications")

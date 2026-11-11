"""Merge RR.HH. and Correspondence migration branches.

This revision only joins the two already-applied histories. It intentionally
does not create, alter, seed, or remove database objects.
"""

revision = "m1b2c3d4e5f6"
down_revision = (
    "h1b2c3d4e5f6",
    "l1b2c3d4e5f6",
)
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass

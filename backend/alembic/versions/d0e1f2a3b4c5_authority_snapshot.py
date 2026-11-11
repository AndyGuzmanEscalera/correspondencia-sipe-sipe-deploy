"""Freeze EDIE authority data independently of future settings/master-data edits.

Revision ID: d0e1f2a3b4c5
Revises: b8c9d0e1f2a3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d0e1f2a3b4c5"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    if "institutional_authority_snapshot" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("correspondences")}:
        op.add_column("correspondences", sa.Column("institutional_authority_snapshot", postgresql.JSONB(), nullable=True))
    # Legacy rows have IDs, not original text snapshots. Freeze available data
    # from their original recipient, never from today's authority setting.
    op.execute("""
        WITH first_recipient AS (
            SELECT DISTINCT ON (m.correspondence_id) m.correspondence_id,
                   m.to_user_id, m.to_unit_id
            FROM correspondence_movements m
            WHERE m.movement_type = 'DERIVED' AND m.cancelled_at IS NULL
            ORDER BY m.correspondence_id, m.sequence_number
        )
        UPDATE correspondences c
        SET institutional_authority_snapshot = jsonb_build_object(
            'employee_id', e.id::text, 'user_id', u.id::text,
            'unit_id', ou.id::text,
            'employee_name', trim(e.first_name || ' ' || e.last_name),
            'position_name', p.name, 'unit_name', ou.name,
            'para_label', ou.name || ' — ' || trim(e.first_name || ' ' || e.last_name))
        FROM first_recipient m, users u, employees e, positions p,
             organizational_units ou, document_types dt
        WHERE c.id = m.correspondence_id AND c.document_type_id = dt.id
          AND (c.institutional_authority_snapshot IS NULL
               OR c.institutional_authority_snapshot = 'null'::jsonb)
          AND upper(trim(dt.code)) IN ('EDIE', 'S')
          AND u.id = m.to_user_id AND e.id = u.employee_id
          AND p.id = e.position_id AND ou.id = m.to_unit_id
    """)


def downgrade():
    # Historical snapshots are audit evidence; retain them on downgrade.
    pass

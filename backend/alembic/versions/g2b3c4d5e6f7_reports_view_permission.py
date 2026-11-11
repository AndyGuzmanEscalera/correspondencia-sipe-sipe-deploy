"""Additive reports.view grant; roles used exclusively by migration."""
import uuid
from alembic import op
import sqlalchemy as sa
revision = "g2b3c4d5e6f7"
down_revision = "g1b2c3d4e5f6"
branch_labels = None
depends_on = None

def upgrade():
    db = op.get_bind()
    pid = db.scalar(sa.text("SELECT id FROM permissions WHERE code='reports.view'"))
    if pid is None:
        pid = uuid.uuid4()
        db.execute(sa.text("INSERT INTO permissions (id,code,description) VALUES (:id,'reports.view','Consultar y exportar reportes de correspondencia visibles')"), {"id": pid})
    for rid in db.scalars(sa.text("SELECT id FROM roles WHERE code IN ('ADMIN','MANAGER') AND is_active=TRUE")):
        db.execute(sa.text("INSERT INTO role_permissions (role_id,permission_id) VALUES (:r,:p) ON CONFLICT DO NOTHING"), {"r":rid,"p":pid})

def downgrade():
    # Preserve additive grants, including permissions assigned to custom roles.
    pass

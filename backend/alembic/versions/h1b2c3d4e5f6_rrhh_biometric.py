"""Read-only biometric integration and configurable attendance foundation."""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "h1b2c3d4e5f6"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("biometric_devices",
        sa.Column("device_id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200)), sa.Column("ip", sa.String(45), nullable=False),
        sa.Column("port", sa.Integer, nullable=False), sa.Column("serial", sa.String(200)),
        sa.Column("model", sa.String(200)),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("biometric_syncs",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("device_id", sa.String(100), sa.ForeignKey("biometric_devices.device_id"), nullable=False),
        sa.Column("requested_start_date", sa.Date, nullable=False),
        sa.Column("requested_end_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("downloaded_records", sa.Integer, nullable=False),
        sa.Column("new_records", sa.Integer, nullable=False), sa.Column("error_code", sa.String(50)),
        sa.Column("error_message", sa.Text),
        sa.Column("executed_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.CheckConstraint("requested_start_date <= requested_end_date", name="ck_biometric_sync_range"),
        sa.CheckConstraint("status IN ('queued','running','completed','failed')", name="ck_biometric_sync_status"))
    op.create_index("uq_biometric_sync_active", "biometric_syncs", ["device_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued','running')"))
    op.create_table("biometric_employee_links",
        sa.Column("device_id", sa.String(100), sa.ForeignKey("biometric_devices.device_id"), primary_key=True),
        sa.Column("user_id", sa.String(100), primary_key=True),
        sa.Column("funcionario_id", sa.Uuid, sa.ForeignKey("employees.id", ondelete="CASCADE"), nullable=False))
    op.create_table("biometric_records",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("record_id", sa.String(64), nullable=False, unique=True),
        sa.Column("device_id", sa.String(100), sa.ForeignKey("biometric_devices.device_id"), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("funcionario_id", sa.Uuid, sa.ForeignKey("employees.id", ondelete="SET NULL")),
        sa.Column("timestamp", sa.DateTime(timezone=False), nullable=False),
        sa.Column("status", sa.Integer), sa.Column("punch", sa.Integer), sa.Column("uid", sa.Integer),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("source IN ('SOURCE_API','SOURCE_FILE')", name="ck_biometric_record_source"))
    op.create_index("ix_biometric_employee_timestamp", "biometric_records", ["funcionario_id", "timestamp", "record_id"])
    op.create_index("ix_biometric_device_timestamp", "biometric_records", ["device_id", "timestamp"])
    names = ("morning_entry_tolerance", "morning_exit_tolerance", "afternoon_entry_tolerance",
             "afternoon_exit_tolerance", "minimum_return_interval", "duplicate_window")
    op.create_table("attendance_parameters",
        sa.Column("id", sa.Integer, primary_key=True),
        *(sa.Column(name, sa.Integer) for name in names),
        sa.Column("updated_by", sa.Uuid, sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("id = 1", name="ck_attendance_parameters_singleton"),
        *(sa.CheckConstraint(f"{name} BETWEEN 0 AND 86400", name=f"ck_attendance_{name}") for name in names))
    op.create_table("attendance_contract_policies",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("cutoff_day", sa.Integer),
        sa.CheckConstraint("cutoff_day BETWEEN 1 AND 31", name="ck_attendance_cutoff"))
    # No guessed attendance values, contract names or user-number grants.
    db = op.get_bind()
    for code, description in (("rrhh.read", "Consultar marcaciones y configuración RRHH"),
                               ("rrhh.manage", "Sincronizar, importar, vincular y configurar RRHH")):
        pid = db.scalar(sa.text("SELECT id FROM permissions WHERE code=:code"), {"code": code})
        if pid is None:
            pid = uuid.uuid4()
            db.execute(sa.text("INSERT INTO permissions (id,code,description) VALUES (:id,:code,:description)"),
                       {"id": pid, "code": code, "description": description})
        for rid in db.scalars(sa.text("SELECT id FROM roles WHERE code='ADMIN' AND is_active=TRUE")):
            db.execute(sa.text("INSERT INTO role_permissions (role_id,permission_id) VALUES (:r,:p) ON CONFLICT DO NOTHING"),
                       {"r": rid, "p": pid})


def downgrade():
    for table in ("attendance_contract_policies", "attendance_parameters", "biometric_records",
                  "biometric_employee_links", "biometric_syncs", "biometric_devices"):
        op.drop_table(table)
    # Keep RBAC grants consistent with additive institutional migrations.

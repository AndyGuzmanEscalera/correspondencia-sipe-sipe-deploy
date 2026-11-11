"""Read-only data access for attendance reports; no biometric service dependency."""
from datetime import datetime, time

from pydantic import ValidationError
from sqlalchemy import inspect, select
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position
from app.modules.rrhh.evaluation_models import EmployeeHeader, EvaluationParameters, RawMark
from app.modules.rrhh.models import ContractPolicy, Parameters, Record
from app.modules.rrhh.shift_source import ExistingShiftReader


class AttendanceRepository:
    def __init__(self, db):
        self.db = db

    def policy(self, contract_type_id):
        if not inspect(self.db.connection()).has_table(ContractPolicy.__tablename__):
            return None
        return self.db.get(ContractPolicy, contract_type_id)

    def parameters(self):
        if not inspect(self.db.connection()).has_table(Parameters.__tablename__):
            return None, None
        row = self.db.get(Parameters, 1)
        if row is None:
            return None, None
        try:
            return EvaluationParameters(**{key: getattr(row, key) for key in EvaluationParameters.model_fields}), row.updated_at
        except ValidationError:
            return None, row.updated_at

    def current_shifts(self, employee_ids):
        return ExistingShiftReader(self.db).current_assignments(employee_ids)

    def employees(self, employee_ids):
        if not employee_ids:
            return {}
        rows = self.db.execute(select(Employee, OrganizationalUnit.name, Position.name, Position.code)
            .outerjoin(OrganizationalUnit, Employee.unit_id == OrganizationalUnit.id)
            .outerjoin(Position, Employee.position_id == Position.id).where(Employee.id.in_(employee_ids))).all()
        return {employee.id: EmployeeHeader(id=employee.id, full_name=f"{employee.first_name} {employee.last_name}",
            document_number=employee.document_number, unit_id=employee.unit_id, unit_name=unit_name,
            position_id=employee.position_id, position_name=position_name, position_code=position_code)
            for employee, unit_name, position_name, position_code in rows}

    def records(self, employee_ids, period):
        if not employee_ids:
            return {}
        rows = self.db.scalars(select(Record).where(Record.funcionario_id.in_(employee_ids),
            Record.timestamp >= datetime.combine(period.start_date, time.min),
            Record.timestamp < datetime.combine(period.end_exclusive, time.min))
            .order_by(Record.timestamp, Record.record_id))
        records = {}
        for row in rows:
            record = RawMark(**{key: getattr(row, key) for key in RawMark.model_fields})
            records.setdefault(row.funcionario_id, []).append(record)
        return records

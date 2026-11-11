"""Explicit, ephemeral QA inputs for the existing attendance source boundary.

No seeds, SQL writes, synthetic marks, coverage assertions or alternate evaluator.
"""
import re
import uuid
from datetime import date, time, timedelta
from pathlib import Path

from pydantic import Field, model_validator
from sqlalchemy import select

from app.modules.rrhh.attendance_source import WorkforceData
from app.modules.rrhh.errors import problem
from app.modules.rrhh.evaluation_models import (
    ContractCatalog, ContractOption, DaySchedule, EvaluationPeriod, EventKind, FrozenModel,
)
from app.modules.rrhh.shift_source import ExistingShiftReader, schedule_from_shift


class SelectedEmployee(FrozenModel):
    employee_id: uuid.UUID
    biometric_user_id: str = Field(min_length=1, max_length=100)
    shift_id: uuid.UUID
    # Every date must be explicit; missing dates never become days off.
    working_days: dict[date, bool]
    session_kinds: tuple[tuple[EventKind, EventKind], ...]
    windows: dict[EventKind, tuple[time, time]]


class StagingConfiguration(FrozenModel):
    purpose: str
    database_name: str
    device_id: str = Field(min_length=1, max_length=100)
    contract_type_id: uuid.UUID
    contract_name: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1901, le=9998)
    month: int = Field(ge=1, le=12)
    period: EvaluationPeriod
    employees: tuple[SelectedEmployee, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def controlled_scope(self):
        if self.purpose != "controlled_rrhh_staging":
            raise ValueError("Explicit staging purpose required")
        if not re.search(r"(^|_)(qa|test|staging)(_|$)", self.database_name):
            raise ValueError("A specifically named disposable QA/test/staging database is required")
        length = (self.period.end_exclusive - self.period.start_date).days
        if length not in {2, 3} or (self.year, self.month) != (self.period.start_date.year, self.period.start_date.month):
            raise ValueError("Select two or three days and the start month/year")
        dates = {self.period.start_date + timedelta(days=i) for i in range(length)}
        if len({row.employee_id for row in self.employees}) != len(self.employees):
            raise ValueError("Repeated employees")
        if len({row.biometric_user_id for row in self.employees}) != len(self.employees):
            raise ValueError("Repeated biometric identifiers")
        for employee in self.employees:
            if set(employee.working_days) != dates:
                raise ValueError("Explicit working/nonworking decision required for every day")
        return self


class StagingAttendanceSource:
    def __init__(self, config):
        self.config = config

    @classmethod
    def from_file(cls, path):
        return cls(StagingConfiguration.model_validate_json(Path(path).read_text(encoding="utf-8")))

    def check_database(self, db):
        if db.get_bind().url.database != self.config.database_name:
            raise problem("staging_database_mismatch", "La base activa no coincide con la base descartable configurada.", 503)

    def contracts(self, db):
        self.check_database(db)
        return ContractCatalog(items=(ContractOption(id=self.config.contract_type_id,
            name="[PRUEBA STAGING] " + self.config.contract_name),))

    def resolve_period(self, db, policy, year, month):
        self.check_database(db)
        if (year, month) != (self.config.year, self.config.month):
            raise problem("staging_period_mismatch", "Seleccione el mes y año de la prueba configurada.", 422)
        return self.config.period

    def load(self, db, contract_type_id, period):
        self.check_database(db)
        if contract_type_id != self.config.contract_type_id or period != self.config.period:
            raise problem("staging_scope_mismatch", "Contrato o período fuera de la prueba configurada.", 422)
        ids = tuple(row.employee_id for row in self.config.employees)
        shifts = ExistingShiftReader(db).current_assignments(ids)
        schedules, pending = {}, []
        from app.modules.rrhh.models import EmployeeLink
        links = {row.user_id: row.funcionario_id for row in db.scalars(select(EmployeeLink).where(
            EmployeeLink.device_id == self.config.device_id,
            EmployeeLink.user_id.in_([row.biometric_user_id for row in self.config.employees])))}
        for row in self.config.employees:
            if links.get(row.biometric_user_id) != row.employee_id:
                pending.append("biometric_employee_link")
            shift = shifts.get(row.employee_id)
            if shift is None or shift.id != row.shift_id or not shift.active:
                pending.append("employee_shift_source")
                continue
            days = {}
            for day, working in row.working_days.items():
                try:
                    days[day] = (schedule_from_shift(day, shift, row.session_kinds, row.windows) if working else
                        DaySchedule(day=day, shift_id=str(shift.id), name=shift.name))
                except (ValueError, KeyError):
                    pending.append("association_windows")
            schedules[row.employee_id] = days
        return WorkforceData(employee_ids=ids,
            contracts={key: (contract_type_id, "[PRUEBA STAGING] " + self.config.contract_name) for key in ids},
            schedules=schedules, pending_configuration=tuple(dict.fromkeys(pending)))

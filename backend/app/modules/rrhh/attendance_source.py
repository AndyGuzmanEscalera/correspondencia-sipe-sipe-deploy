"""Adapter boundary for confirmed institutional contracts, shifts and coverage.

The ORM omits the optional historical work_shifts tables which do exist in the
local database. ExistingShiftReader reuses those exact tables, but their current
assignments don't establish historical workdays, windows or contract membership.
Implement the missing confirmed policy here and override get_attendance_source
through the normal FastAPI dependency system. No guessed table is queried.
"""
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Protocol

from app.modules.rrhh.evaluation_models import ContractCatalog, DaySchedule, EvaluationPeriod


@dataclass(frozen=True)
class WorkforceData:
    # None = missing source; an empty tuple = confirmed empty contract population.
    employee_ids: tuple[uuid.UUID, ...] | None = None
    contracts: Mapping[uuid.UUID, tuple[uuid.UUID, str]] = field(default_factory=dict)
    schedules: Mapping[uuid.UUID, Mapping[date, DaySchedule]] = field(default_factory=dict)
    coverage: Mapping[uuid.UUID, Mapping[date, bool | None]] = field(default_factory=dict)
    pending_configuration: tuple[str, ...] = ()


class AttendanceSource(Protocol):
    def contracts(self, db) -> ContractCatalog:
        """Only real, confirmed institutional contract options."""
        ...

    def resolve_period(self, db, policy, year: int, month: int) -> EvaluationPeriod | None:
        """Return a confirmed period. policy may be None; cutoff alone is insufficient."""
        ...

    def load(self, db, contract_type_id: uuid.UUID, period: EvaluationPeriod) -> WorkforceData:
        """Batch load population, historical assignments/windows and optional coverage."""
        ...


class PendingAttendanceSource:
    def contracts(self, db):
        return ContractCatalog(pending_configuration=("employee_contract_assignment", "confirmed_contract_period_policy"))

    def resolve_period(self, db, policy, year, month):
        return None

    def load(self, db, contract_type_id, period):
        return WorkforceData(pending_configuration=("employee_contract_assignment", "employee_shift_source",
                                                     "association_windows"))


def get_attendance_source() -> AttendanceSource:
    import os
    from dotenv import load_dotenv
    load_dotenv(".env", override=False)
    if os.getenv("RRHH_STAGING_CONFIG", "").strip():
        from app.core.config import get_settings
        from app.modules.rrhh.staging_source import StagingAttendanceSource
        from app.modules.rrhh.errors import problem
        if get_settings().app_env != "qa":
            raise problem("staging_configuration_forbidden", "La configuración temporal RRHH requiere APP_ENV=qa.", 503)
        try:
            return StagingAttendanceSource.from_file(os.environ["RRHH_STAGING_CONFIG"])
        except (ValueError, OSError):
            raise problem("staging_configuration_invalid", "Revise el archivo temporal RRHH de QA.", 503) from None
    return PendingAttendanceSource()

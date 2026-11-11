"""Immutable input/output contracts for reproducible attendance interpretation."""
import uuid
from datetime import date, datetime, time
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ENGINE_VERSION = "1.0.0"


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DayStatus(str, Enum):
    NORMAL = "NORMAL"
    OBSERVATION = "OBSERVATION"
    ANOMALY = "ANOMALY"
    NO_DATA = "NO_DATA"


class Severity(str, Enum):
    INFO = "INFO"
    OBSERVATION = "OBSERVATION"
    ANOMALY = "ANOMALY"


class IncidentType(str, Enum):
    LATE = "LATE"
    EARLY_EXIT = "EARLY_EXIT"
    LATE_RETURN = "LATE_RETURN"
    MISSING_MARK = "MISSING_MARK"
    EXTRA_MARK = "EXTRA_MARK"
    POSSIBLE_DUPLICATE = "POSSIBLE_DUPLICATE"
    INTERMEDIATE_EXIT = "INTERMEDIATE_EXIT"
    MINIMUM_BREAK_VIOLATION = "MINIMUM_BREAK_VIOLATION"
    INCONSISTENT_SEQUENCE = "INCONSISTENT_SEQUENCE"


class EventKind(str, Enum):
    MORNING_ENTRY = "morning_entry"
    MORNING_EXIT = "morning_exit"
    AFTERNOON_ENTRY = "afternoon_entry"
    FINAL_EXIT = "final_exit"

    @property
    def is_entry(self):
        return self in {self.MORNING_ENTRY, self.AFTERNOON_ENTRY}


class RawMark(FrozenModel):
    record_id: str
    device_id: str
    user_id: str
    funcionario_id: uuid.UUID
    timestamp: datetime
    status: int | None = None
    punch: int | None = None
    uid: int | None = None
    source: str

    @field_validator("timestamp")
    @classmethod
    def local_time(cls, value):
        if value.tzinfo is not None or value.microsecond:
            raise ValueError("Expected local device time with second precision")
        return value


class ExpectedEvent(FrozenModel):
    kind: EventKind
    expected_at: datetime
    window_start: datetime
    window_end: datetime

    @model_validator(mode="after")
    def valid_window(self):
        values = (self.expected_at, self.window_start, self.window_end)
        if any(value.tzinfo is not None or value.microsecond for value in values):
            raise ValueError("Expected local second-precision schedule times")
        if not self.window_start <= self.expected_at <= self.window_end:
            raise ValueError("Association window must include the expected event")
        return self


class DaySchedule(FrozenModel):
    day: date
    shift_id: str
    name: str
    events: tuple[ExpectedEvent, ...] = ()

    @model_validator(mode="after")
    def coherent_schedule(self):
        # Zero events is an explicitly non-working day, not a missing assignment.
        kinds = [event.kind for event in self.events]
        allowed = [EventKind.MORNING_ENTRY, EventKind.MORNING_EXIT,
                   EventKind.AFTERNOON_ENTRY, EventKind.FINAL_EXIT]
        if kinds and kinds not in (allowed, allowed[:2], allowed[2:], [allowed[0], allowed[3]]):
            raise ValueError("Expected one or two complete work sessions, up to four events")
        times = [event.expected_at for event in self.events]
        if any(a >= b for a, b in zip(times, times[1:])):
            raise ValueError("Expected events must be strictly chronological")
        if any(value.date() != self.day for event in self.events
               for value in (event.expected_at, event.window_start, event.window_end)):
            raise ValueError("Overnight shifts require a confirmed cross-day adapter")
        return self


class EvaluationParameters(FrozenModel):
    # Same names and seconds as the existing PostgreSQL Parameters model.
    morning_entry_tolerance: int = Field(ge=0, le=86400, strict=True)
    morning_exit_tolerance: int = Field(ge=0, le=86400, strict=True)
    afternoon_entry_tolerance: int = Field(ge=0, le=86400, strict=True)
    afternoon_exit_tolerance: int = Field(ge=0, le=86400, strict=True)
    minimum_return_interval: int = Field(ge=0, le=86400, strict=True)
    duplicate_window: int = Field(ge=0, le=86400, strict=True)

    def tolerance(self, kind):
        return getattr(self, {EventKind.FINAL_EXIT: "afternoon_exit_tolerance"}.get(kind, kind.value + "_tolerance"))


class Suggestion(FrozenModel):
    description: str
    record_ids: tuple[str, ...]
    suggested_record_id: str | None = None


class Incident(FrozenModel):
    type: IncidentType
    severity: Severity
    description: str
    seconds: int | None = None
    minutes: float | None = None
    record_ids: tuple[str, ...] = ()
    event_kind: EventKind | None = None
    suggestion: Suggestion | None = None


class EventAssessment(FrozenModel):
    expected: ExpectedEvent
    record_id: str | None = None
    recorded_at: datetime | None = None
    candidate_record_ids: tuple[str, ...] = ()
    association: str
    status: str | None = None
    late_seconds: int | None = None
    gross_early_exit_seconds: int | None = None
    # With nonzero tolerance the exit policy is explicitly pending confirmation.
    early_exit_seconds: int | None = None
    exit_tolerance_policy_pending: bool = False


class IntermediateInterval(FrozenModel):
    possible_exit_at: datetime
    possible_return_at: datetime
    duration_seconds: int
    record_ids: tuple[str, str]
    requires_review: bool = True


class DailyEvaluation(FrozenModel):
    date: date
    status: DayStatus
    schedule: DaySchedule | None
    coverage: bool | None
    original_records: tuple[RawMark, ...]
    events: tuple[EventAssessment, ...] = ()
    morning_entry: datetime | None = None
    morning_exit: datetime | None = None
    afternoon_entry: datetime | None = None
    final_exit: datetime | None = None
    late_seconds: int | None = None
    late_minutes: float | None = None
    gross_early_exit_seconds: int | None = None
    gross_early_exit_minutes: float | None = None
    early_exit_seconds: int | None = None
    early_exit_minutes: float | None = None
    observed_late_seconds: int = 0
    observed_gross_early_exit_seconds: int = 0
    absence_minutes: float | None = None
    non_evaluable_minutes: float | None = None
    overtime_minutes: float | None = None
    reconstruction_complete: bool = False
    reason: str | None = None
    pending_configuration: tuple[str, ...] = ()
    incidents: tuple[Incident, ...] = ()
    suggestions: tuple[Suggestion, ...] = ()
    intermediate_intervals: tuple[IntermediateInterval, ...] = ()


class CalendarDay(FrozenModel):
    date: date
    status: DayStatus
    total_records: int
    first_mark: datetime | None
    last_mark: datetime | None
    incident_count: int


class PeriodSummary(FrozenModel):
    days_evaluated: int
    normal_days: int
    observation_days: int
    anomaly_days: int
    no_data_days: int
    total_records: int
    total_incidents: int
    total_late_seconds: int | None
    total_late_minutes: float | None
    total_gross_early_exit_seconds: int | None
    total_gross_early_exit_minutes: float | None
    total_early_exit_seconds: int | None
    total_early_exit_minutes: float | None
    observed_late_seconds: int
    observed_gross_early_exit_seconds: int
    totals_complete: bool


class EvaluationPeriod(FrozenModel):
    start_date: date
    end_exclusive: date

    @model_validator(mode="after")
    def bounded_period(self):
        if not 0 < (self.end_exclusive - self.start_date).days <= 367:
            raise ValueError("Expected a positive period of at most 367 days")
        return self


class EmployeeHeader(FrozenModel):
    id: uuid.UUID
    full_name: str
    document_number: str | None
    item_code: str | None = None
    unit_id: uuid.UUID | None = None
    unit_name: str | None = None
    position_id: uuid.UUID | None = None
    position_name: str | None = None
    position_code: str | None = None
    contract_type_id: uuid.UUID | None = None
    contract_type_name: str | None = None


class ShiftHeader(FrozenModel):
    id: str
    name: str


class ShiftSegment(FrozenModel):
    sequence: int
    start_time: time
    end_time: time
    ends_next_day: bool


class ShiftDefinition(FrozenModel):
    id: uuid.UUID
    name: str
    active: bool
    segments: tuple[ShiftSegment, ...]
    assignment_scope: str = "current_unversioned"


class IndividualReport(FrozenModel):
    engine_version: str = ENGINE_VERSION
    ready: bool
    employee: EmployeeHeader
    period: EvaluationPeriod | None
    period_confirmed: bool = False
    parameters: EvaluationParameters | None = None
    parameters_updated_at: datetime | None = None
    shifts: tuple[ShiftHeader, ...] = ()
    current_shift: ShiftDefinition | None = None
    biometric_coverage: str = "unknown"
    pending_configuration: tuple[str, ...] = ()
    pending_policies: tuple[str, ...] = ("absence_policy", "overtime_policy", "exit_tolerance_semantics")
    summary: PeriodSummary | None = None
    days: tuple[DailyEvaluation, ...] = ()
    calendar: tuple[CalendarDay, ...] = ()


class EmployeeEvaluationSummary(FrozenModel):
    employee: EmployeeHeader
    ready: bool
    summary: PeriodSummary | None
    pending_configuration: tuple[str, ...]
    status: DayStatus = DayStatus.NO_DATA
    shifts: tuple[ShiftHeader, ...] = ()


class ContractOption(FrozenModel):
    id: uuid.UUID
    name: str


class ContractCatalog(FrozenModel):
    items: tuple[ContractOption, ...] = ()
    pending_configuration: tuple[str, ...] = ()


class GenerationResult(FrozenModel):
    engine_version: str = ENGINE_VERSION
    ready: bool
    contract_type_id: uuid.UUID
    year: int
    month: int
    period: EvaluationPeriod | None
    period_confirmed: bool = False
    pending_configuration: tuple[str, ...] = ()
    biometric_coverage: str = "unknown"
    employees: tuple[EmployeeEvaluationSummary, ...] = ()
    total_employees: int | None = None
    message: str

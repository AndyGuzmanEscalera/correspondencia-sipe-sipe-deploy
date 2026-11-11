"""Read existing optional shift tables without registering/creating duplicate models.

Confirmed against the local shared database: employees.work_shift_id -> work_shifts;
segments have sequence/start_time/end_time/ends_next_day. The released ORM omits
these optional artifacts, so inspect their presence before querying them.
"""
from datetime import datetime, time
from typing import Mapping

from sqlalchemy import bindparam, inspect, text
from app.modules.rrhh.evaluation_models import DaySchedule, EventKind, ExpectedEvent, ShiftDefinition, ShiftSegment


class ExistingShiftReader:
    def __init__(self, db):
        self.db = db

    def current_assignments(self, employee_ids):
        if not employee_ids:
            return {}
        schema = inspect(self.db.connection())
        required = {"employees": {"id", "work_shift_id"},
                    "work_shifts": {"id", "name", "is_active"},
                    "work_shift_segments": {"work_shift_id", "sequence", "start_time", "end_time", "ends_next_day"}}
        for table, columns in required.items():
            if not schema.has_table(table) or not columns.issubset({c["name"] for c in schema.get_columns(table)}):
                return {}  # Not every installation contains the optional historical shift module.
        query = text("""
            SELECT e.id AS employee_id, w.id AS shift_id, w.name, w.is_active,
                   s.sequence, s.start_time, s.end_time, s.ends_next_day
            FROM employees e JOIN work_shifts w ON w.id = e.work_shift_id
            LEFT JOIN work_shift_segments s ON s.work_shift_id = w.id
            WHERE e.id IN :employee_ids ORDER BY e.id, s.sequence
        """).bindparams(bindparam("employee_ids", expanding=True))
        result = {}
        for row in self.db.execute(query, {"employee_ids": tuple(employee_ids)}).mappings():
            entry = result.setdefault(row["employee_id"], {"id": row["shift_id"], "name": row["name"],
                                                          "active": row["is_active"], "segments": []})
            if row["sequence"] is not None:
                entry["segments"].append(ShiftSegment(sequence=row["sequence"], start_time=row["start_time"],
                    end_time=row["end_time"], ends_next_day=row["ends_next_day"]))
        return {employee_id: ShiftDefinition(**entry) for employee_id, entry in result.items()}


def schedule_from_shift(day, shift, session_kinds, windows: Mapping[EventKind, tuple[time, time]]):
    """Explicit adapter helper: caller confirms day/validity, event roles and windows.

    It never guesses weekdays, session meanings or midnight handling from a name.
    """
    if not shift.active or len(shift.segments) not in {1, 2} or len(session_kinds) != len(shift.segments):
        raise ValueError("Active shift with one or two confirmed sessions required")
    if any(segment.ends_next_day for segment in shift.segments):
        raise ValueError("Overnight assignment/calendar policy is pending")
    if len({segment.sequence for segment in shift.segments}) != len(shift.segments):
        raise ValueError("Ambiguous shift segment sequence")
    events = []
    for segment, kinds in zip(sorted(shift.segments, key=lambda value: value.sequence), session_kinds):
        if len(kinds) != 2:
            raise ValueError("Each session needs confirmed entry/exit roles")
        for kind, expected in zip(kinds, (segment.start_time, segment.end_time)):
            lower, upper = windows[kind]
            events.append(ExpectedEvent(kind=kind, expected_at=datetime.combine(day, expected),
                window_start=datetime.combine(day, lower), window_end=datetime.combine(day, upper)))
    return DaySchedule(day=day, shift_id=str(shift.id), name=shift.name, events=tuple(events))

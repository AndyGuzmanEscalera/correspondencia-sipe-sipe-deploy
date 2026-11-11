"""Pure, conservative attendance evaluator. No SQL, hardware or record mutations."""
from collections import Counter
from datetime import timedelta

from app.modules.rrhh.evaluation_models import (
    CalendarDay, DailyEvaluation, DayStatus, EventAssessment, EventKind,
    Incident, IncidentType, IntermediateInterval, PeriodSummary, Severity, Suggestion,
)


def event_metrics(event, mark, parameters):
    """Directional comparison; seconds are authoritative, no rounding policy invented."""
    tolerance = parameters.tolerance(event.kind)
    if event.kind.is_entry:
        late = max(0, int((mark.timestamp - event.expected_at).total_seconds()) - tolerance)
        code = IncidentType.LATE_RETURN if event.kind == EventKind.AFTERNOON_ENTRY else IncidentType.LATE
        return {"status": code.value if late else "NORMAL", "late_seconds": late,
                "gross_early_exit_seconds": 0, "early_exit_seconds": 0,
                "exit_tolerance_policy_pending": False}
    gross = max(0, int((event.expected_at - mark.timestamp).total_seconds()))
    pending = gross > 0 and tolerance > 0
    return {"status": IncidentType.EARLY_EXIT.value if gross else "NORMAL", "late_seconds": 0,
            "gross_early_exit_seconds": gross, "early_exit_seconds": None if pending else gross,
            "exit_tolerance_policy_pending": pending}


def _incident(kind, severity, description, records=(), seconds=None, event=None, suggestion=None):
    return Incident(type=kind, severity=severity, description=description, seconds=seconds,
                    minutes=seconds / 60 if seconds is not None else None,
                    record_ids=tuple(mark.record_id for mark in records), event_kind=event, suggestion=suggestion)


def _candidate_slots(mark, events):
    return tuple(i for i, event in enumerate(events) if event.window_start <= mark.timestamp <= event.window_end)


def _units_and_duplicates(marks, events, window):
    """Flag repetitions; bounded span from the first mark, never transitive chaining.

    Matching device/user/raw codes and candidate windows is only a compatibility
    signal, never an interpretation of status/punch. A different expected event
    window breaks the group, so a scheduled break isn't treated as a duplicate.
    """
    units = []
    for mark in marks:
        if units and window > 0:
            anchor = units[-1][0]
            compatible = ((mark.device_id, mark.user_id, mark.status, mark.punch) ==
                          (anchor.device_id, anchor.user_id, anchor.status, anchor.punch))
            same_slots = _candidate_slots(mark, events) == _candidate_slots(anchor, events)
            spans_events = sum(anchor.timestamp <= e.expected_at <= mark.timestamp for e in events) > 1
            if compatible and same_slots and not spans_events and (mark.timestamp - anchor.timestamp).total_seconds() <= window:
                units[-1].append(mark)
                continue
        units.append([mark])
    return units


def _optimal_candidates(events, units):
    """All maximum chronological matchings in O(expected_events * marks).

    No nearest-time winner is forced. A slot is firm only if every maximum
    matching assigns the same unit and never leaves that slot empty.
    """
    m, n = len(events), len(units)
    compatible = [[all(event.window_start <= mark.timestamp <= event.window_end for mark in unit)
                   for unit in units] for event in events]
    prefix = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m):
        for j in range(n):
            prefix[i + 1][j + 1] = max(prefix[i][j + 1], prefix[i + 1][j],
                                        prefix[i][j] + int(compatible[i][j]))
    suffix = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(n - 1, -1, -1):
            suffix[i][j] = max(suffix[i + 1][j], suffix[i][j + 1],
                               suffix[i + 1][j + 1] + int(compatible[i][j]))
    maximum = prefix[m][n]
    result = []
    for i in range(m):
        choices = [j for j in range(n) if compatible[i][j] and
                   prefix[i][j] + 1 + suffix[i + 1][j + 1] == maximum]
        may_be_missing = any(prefix[i][k] + suffix[i + 1][k] == maximum for k in range(n + 1))
        result.append((choices, may_be_missing))
    return result


def evaluate_day(employee_id, day, schedule, parameters, records, coverage=None):
    marks = tuple(sorted(records, key=lambda mark: (mark.timestamp, mark.record_id)))
    if any(mark.funcionario_id != employee_id or mark.timestamp.date() != day for mark in marks):
        raise ValueError("Records must belong to the evaluated employee and local day")
    if len({mark.record_id for mark in marks}) != len(marks):
        raise ValueError("Repeated input record identity")
    if schedule is not None and schedule.day != day:
        raise ValueError("Schedule belongs to another day")
    base = {"date": day, "original_records": marks, "schedule": schedule, "coverage": coverage}
    if schedule is None:
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason="missing_shift_assignment",
                               pending_configuration=("employee_shift_source",))
    if parameters is None:
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason="missing_parameters",
                               pending_configuration=("attendance_parameters",))
    if coverage is False:
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason="outside_confirmed_coverage")
    if not schedule.events:
        return DailyEvaluation(**base, status=DayStatus.NO_DATA, reason="no_expected_work_session")

    events = schedule.events
    units = _units_and_duplicates(marks, events, parameters.duplicate_window)
    incidents, suggestions, assessments = [], [], []
    candidates = _optimal_candidates(events, units)
    candidate_units = {index for choices, _ in candidates for index in choices}
    for unit in units:
        if len(unit) > 1:
            compatible_events = [event for event in events if all(
                event.window_start <= mark.timestamp <= event.window_end for mark in unit)]
            # Nearest is only a clearly labeled suggestion; it never becomes an assignment.
            principal = min(unit, key=lambda mark: (min(abs((mark.timestamp - event.expected_at).total_seconds())
                            for event in compatible_events), mark.record_id)) if len(compatible_events) == 1 else None
            suggestion = Suggestion(description="Revisar las marcas cercanas; la selección principal es solo una sugerencia.",
                                    record_ids=tuple(mark.record_id for mark in unit),
                                    suggested_record_id=principal.record_id if principal else None)
            suggestions.append(suggestion)
            incidents.append(_incident(IncidentType.POSSIBLE_DUPLICATE, Severity.OBSERVATION,
                f"Se detectaron {len(unit)} marcaciones cercanas entre {unit[0].timestamp:%H:%M:%S} y {unit[-1].timestamp:%H:%M:%S}.",
                unit, suggestion=suggestion))

    firm = {}
    for event, (choices, may_be_missing) in zip(events, candidates):
        possible = tuple(mark for index in choices for mark in units[index])
        association = "MISSING" if not choices else "AMBIGUOUS"
        fields = {}
        selected = None
        if len(choices) == 1 and not may_be_missing:
            unit = units[choices[0]]
            if len(unit) == 1:
                selected = unit[0]
                firm[event.kind] = selected
                fields = event_metrics(event, selected, parameters)
                association = "ASSIGNED"
            else:
                association = "POSSIBLE_DUPLICATE"
                metrics = [event_metrics(event, mark, parameters) for mark in unit]
                # Penalties are reported only if every candidate yields the same result.
                fields = {key: metrics[0][key] for key in metrics[0]
                          if all(metric[key] == metrics[0][key] for metric in metrics)}
                fields["exit_tolerance_policy_pending"] = any(metric["exit_tolerance_policy_pending"] for metric in metrics)
        if association == "MISSING":
            incidents.append(_incident(IncidentType.MISSING_MARK, Severity.ANOMALY,
                "No hay una marcación asociable al evento esperado; no se presume ausencia ni justificación.", event=event.kind))
        elif association == "AMBIGUOUS":
            incidents.append(_incident(IncidentType.INCONSISTENT_SEQUENCE, Severity.ANOMALY,
                "La secuencia permite varias asociaciones; no se eligió una automáticamente.", possible, event=event.kind))
        # Only a definitive timestamp generates a definite late/early incident.
        if selected and fields["status"] != "NORMAL":
            kind = IncidentType(fields["status"])
            seconds = fields["late_seconds"] if event.kind.is_entry else fields["gross_early_exit_seconds"]
            description = ("Entrada posterior al límite de tolerancia." if event.kind.is_entry else
                           "Salida anterior al horario; el tiempo bruto no implica un descuento confirmado.")
            incidents.append(_incident(kind, Severity.OBSERVATION, description, (selected,), seconds, event.kind))
        assessments.append(EventAssessment(expected=event, record_id=selected.record_id if selected else None,
            recorded_at=selected.timestamp if selected else None,
            candidate_record_ids=tuple(mark.record_id for mark in possible), association=association, **fields))

    extras = [unit for index, unit in enumerate(units) if index not in candidate_units]
    for unit in extras:
        incidents.append(_incident(IncidentType.EXTRA_MARK, Severity.INFO,
            "Marcación adicional sin evento principal asignado; se conserva sin inferir una causa.", unit))
    intervals = []
    extra_singletons = [unit[0] for unit in extras if len(unit) == 1]
    first = next((firm.get(event.kind) for event in events if event.kind.is_entry), None)
    last = next((firm.get(event.kind) for event in reversed(events) if not event.kind.is_entry), None)
    if len(extra_singletons) == 2 and first and last:
        departure, arrival = extra_singletons
        if first.timestamp < departure.timestamp < arrival.timestamp < last.timestamp:
            duration = int((arrival.timestamp - departure.timestamp).total_seconds())
            intervals.append(IntermediateInterval(possible_exit_at=departure.timestamp,
                possible_return_at=arrival.timestamp, duration_seconds=duration,
                record_ids=(departure.record_id, arrival.record_id)))
            incidents.append(_incident(IncidentType.INTERMEDIATE_EXIT, Severity.ANOMALY,
                "Posible salida y retorno intermedios por secuencia temporal; requiere revisión y no indica una causa.",
                (departure, arrival), duration))
            if duration < parameters.minimum_return_interval:
                incidents.append(_incident(IncidentType.MINIMUM_BREAK_VIOLATION, Severity.ANOMALY,
                    "El intervalo sugerido de salida/retorno es menor al mínimo configurado.", (departure, arrival), duration))
    if len(extra_singletons) > 1 and not intervals:
        incidents.append(_incident(IncidentType.INCONSISTENT_SEQUENCE, Severity.ANOMALY,
            "Hay marcaciones adicionales que no permiten reconstruir una secuencia única.", extra_singletons))
    departure, arrival = firm.get(EventKind.MORNING_EXIT), firm.get(EventKind.AFTERNOON_ENTRY)
    if departure and arrival:
        elapsed = int((arrival.timestamp - departure.timestamp).total_seconds())
        if elapsed < parameters.minimum_return_interval:
            incidents.append(_incident(IncidentType.MINIMUM_BREAK_VIOLATION, Severity.ANOMALY,
                "El intervalo entre salida y siguiente entrada es menor al mínimo configurado.", (departure, arrival), elapsed))

    def total(field):
        values = [getattr(event, field) for event in assessments]
        return sum(values) if all(value is not None for value in values) else None

    late = total("late_seconds")
    gross = total("gross_early_exit_seconds")
    early = total("early_exit_seconds")
    severity = {incident.severity for incident in incidents}
    status = (DayStatus.NO_DATA if not marks else DayStatus.ANOMALY if Severity.ANOMALY in severity else
              DayStatus.OBSERVATION if Severity.OBSERVATION in severity else DayStatus.NORMAL)
    pending = ("exit_tolerance_semantics",) if any(event.exit_tolerance_policy_pending for event in assessments) else ()
    return DailyEvaluation(**base, status=status, events=tuple(assessments),
        morning_entry=firm.get(EventKind.MORNING_ENTRY).timestamp if EventKind.MORNING_ENTRY in firm else None,
        morning_exit=firm.get(EventKind.MORNING_EXIT).timestamp if EventKind.MORNING_EXIT in firm else None,
        afternoon_entry=firm.get(EventKind.AFTERNOON_ENTRY).timestamp if EventKind.AFTERNOON_ENTRY in firm else None,
        final_exit=firm.get(EventKind.FINAL_EXIT).timestamp if EventKind.FINAL_EXIT in firm else None,
        late_seconds=late, late_minutes=late / 60 if late is not None else None,
        gross_early_exit_seconds=gross, gross_early_exit_minutes=gross / 60 if gross is not None else None,
        early_exit_seconds=early, early_exit_minutes=early / 60 if early is not None else None,
        observed_late_seconds=sum(event.late_seconds or 0 for event in assessments),
        observed_gross_early_exit_seconds=sum(event.gross_early_exit_seconds or 0 for event in assessments),
        reconstruction_complete=all(event.association == "ASSIGNED" for event in assessments) and not intervals and not extras,
        reason="no_records_not_proof_of_absence" if not marks else None,
        pending_configuration=pending, incidents=tuple(incidents), suggestions=tuple(suggestions),
        intermediate_intervals=tuple(intervals))


def period_summary(days):
    counts = Counter(day.status for day in days)
    def total(field):
        # Explicit non-working days carry no penalties but are not evaluated.
        values = [getattr(day, field) for day in days if day.reason != "no_expected_work_session"]
        return sum(values) if values and all(value is not None for value in values) else None
    late, gross, early = (total(field) for field in ("late_seconds", "gross_early_exit_seconds", "early_exit_seconds"))
    return PeriodSummary(days_evaluated=len(days) - counts[DayStatus.NO_DATA],
        normal_days=counts[DayStatus.NORMAL], observation_days=counts[DayStatus.OBSERVATION],
        anomaly_days=counts[DayStatus.ANOMALY], no_data_days=counts[DayStatus.NO_DATA],
        total_records=sum(len(day.original_records) for day in days),
        total_incidents=sum(len(day.incidents) for day in days), total_late_seconds=late,
        total_late_minutes=late / 60 if late is not None else None, total_gross_early_exit_seconds=gross,
        total_gross_early_exit_minutes=gross / 60 if gross is not None else None,
        total_early_exit_seconds=early, total_early_exit_minutes=early / 60 if early is not None else None,
        observed_late_seconds=sum(day.observed_late_seconds for day in days),
        observed_gross_early_exit_seconds=sum(day.observed_gross_early_exit_seconds for day in days),
        totals_complete=all(value is not None for value in (late, gross, early)) and
                        all(day.coverage is True for day in days if day.reason != "no_expected_work_session"))


def calendar_days(days):
    return tuple(CalendarDay(date=day.date, status=day.status, total_records=len(day.original_records),
        first_mark=day.original_records[0].timestamp if day.original_records else None,
        last_mark=day.original_records[-1].timestamp if day.original_records else None,
        incident_count=len(day.incidents)) for day in days)


def evaluate_period(employee_id, period, schedules, parameters, records, coverage=None):
    by_day = {}
    for mark in records:
        if mark.funcionario_id != employee_id or not period.start_date <= mark.timestamp.date() < period.end_exclusive:
            raise ValueError("Records outside evaluated employee or period")
        by_day.setdefault(mark.timestamp.date(), []).append(mark)
    days = []
    day = period.start_date
    while day < period.end_exclusive:
        days.append(evaluate_day(employee_id, day, schedules.get(day), parameters, by_day.get(day, ()),
                                  (coverage or {}).get(day)))
        day += timedelta(days=1)
    return tuple(days)

"""Reproducible report orchestration over existing PostgreSQL evidence."""
from app.modules.rrhh.attendance_repository import AttendanceRepository
from app.modules.rrhh.attendance_source import WorkforceData
from app.modules.rrhh.errors import problem
from app.modules.rrhh.evaluation import calendar_days, evaluate_period, period_summary
from app.modules.rrhh.evaluation_models import (
    DayStatus, EmployeeEvaluationSummary, EvaluationPeriod, GenerationResult, IndividualReport, ShiftHeader,
)
from app.modules.rrhh.periods import period_for


class AttendanceService:
    def __init__(self, db, source, repository=None):
        self.db, self.source = db, source
        self.repository = repository or AttendanceRepository(db)

    def _context(self, contract_type_id, year, month):
        policy = self.repository.policy(contract_type_id)
        parameters, updated_at = self.repository.parameters()
        pending = []
        # A confirmed source may supply an explicit bounded period without a
        # permanent cutoff policy (e.g. an operator-configured QA exercise).
        period = self.source.resolve_period(self.db, policy, year, month)
        preview = period
        if period is None:
            if policy is None or policy.cutoff_day is None:
                pending.append("confirmed_contract_period_policy")
            pending.append("confirmed_contract_period_semantics")
            # Preserve the existing optional period preview, explicitly unconfirmed.
            # It is NEVER used to fetch/evaluate records until an adapter confirms it.
            if policy is not None and policy.cutoff_day is not None:
                start, end = period_for(year, month, policy.cutoff_day)
                preview = EvaluationPeriod(start_date=start, end_exclusive=end)
        data = self.source.load(self.db, contract_type_id, period) if period is not None else WorkforceData(
            pending_configuration=("employee_contract_assignment", "employee_shift_source", "association_windows"))
        pending.extend(data.pending_configuration)
        if data.employee_ids is None:
            pending.append("employee_contract_assignment")
        elif len(set(data.employee_ids)) != len(data.employee_ids):
            raise problem("attendance_source_invalid", "La fuente contractual devolvió funcionarios repetidos.", 503)
        if any(contract[0] != contract_type_id for contract in data.contracts.values()):
            raise problem("attendance_source_invalid", "La fuente devolvió un contrato diferente al solicitado.", 503)
        if parameters is None:
            pending.append("attendance_parameters")
        return period, preview, parameters, updated_at, data, tuple(dict.fromkeys(pending))

    def _report(self, header, period, preview, parameters, updated_at, data, pending, records, current_shift=None):
        contract = data.contracts.get(header.id)
        issues = list(pending)
        if contract is None:
            issues.append("employee_contract_assignment")
        else:
            header = header.model_copy(update={"contract_type_id": contract[0], "contract_type_name": contract[1]})
        can_evaluate = period is not None and data.employee_ids is not None and header.id in data.employee_ids and contract is not None
        days = evaluate_period(header.id, period, data.schedules.get(header.id, {}), parameters,
            records, data.coverage.get(header.id, {})) if can_evaluate else ()
        for day in days:
            issues.extend(key for key in day.pending_configuration if key != "exit_tolerance_semantics")
        # Coverage unknown doesn't stop interpreting available evidence. It never
        # turns missing marks into absence, or a sync timestamp into coverage proof.
        ready = can_evaluate and parameters is not None and not issues
        return IndividualReport(ready=ready, employee=header, period=preview, period_confirmed=period is not None,
            parameters=parameters, parameters_updated_at=updated_at,
            current_shift=current_shift,
            shifts=tuple(ShiftHeader(id=shift_id, name=name) for shift_id, name in dict.fromkeys(
                (day.schedule.shift_id, day.schedule.name) for day in days if day.schedule is not None)),
            biometric_coverage=coverage_status(days),
            pending_configuration=tuple(dict.fromkeys(issues)), days=days,
            summary=period_summary(days) if days else None, calendar=calendar_days(days))

    def generate(self, contract_type_id, year, month):
        period, preview, parameters, updated_at, data, pending = self._context(contract_type_id, year, month)
        ids = data.employee_ids or ()
        headers = self.repository.employees(ids)
        current_shifts = self.repository.current_shifts(ids)
        records = self.repository.records(ids, period) if period is not None else {}
        summaries = []
        coverage_states = []
        all_pending = list(pending)
        for employee_id in ids:
            header = headers.get(employee_id)
            if header is None:
                raise problem("attendance_source_invalid", "La fuente contractual referencia un funcionario inexistente.", 503)
            report = self._report(header, period, preview, parameters, updated_at, data, pending,
                                  records.get(employee_id, ()), current_shifts.get(employee_id))
            coverage_states.append(report.biometric_coverage)
            all_pending.extend(report.pending_configuration)
            summaries.append(EmployeeEvaluationSummary(employee=report.employee, ready=report.ready, summary=report.summary,
                pending_configuration=report.pending_configuration, shifts=report.shifts,
                status=summary_status(report.summary)))
        ready = period is not None and data.employee_ids is not None and not all_pending and all(row.ready for row in summaries)
        # Empty population is valid only when the source explicitly confirms it.
        coverage = ("partial" if "partial" in coverage_states else "confirmed" if coverage_states and
                    all(value in {"confirmed", "not_applicable"} for value in coverage_states) else "unknown")
        return GenerationResult(ready=ready, contract_type_id=contract_type_id, year=year, month=month,
            period=preview, period_confirmed=period is not None,
            pending_configuration=tuple(dict.fromkeys(all_pending)), employees=tuple(summaries),
            biometric_coverage=coverage,
            total_employees=len(ids) if data.employee_ids is not None else None,
            message="Evaluación reproducible de marcaciones locales." if ready else
                    "Faltan dependencias confirmadas; revise pending_configuration y los días no evaluables.")

    def report(self, employee_id, contract_type_id, year, month):
        header = self.repository.employees((employee_id,)).get(employee_id)
        if header is None:
            raise problem("employee_not_found", "Funcionario no encontrado.", 404)
        period, preview, parameters, updated_at, data, pending = self._context(contract_type_id, year, month)
        if data.employee_ids is not None and employee_id not in data.employee_ids:
            raise problem("employee_contract_mismatch", "El funcionario no pertenece al contrato y período solicitados.", 422)
        # Do not query an entire contract's records for an individual report.
        records = self.repository.records((employee_id,), period).get(employee_id, ()) if period is not None else ()
        return self._report(header, period, preview, parameters, updated_at, data, pending, records,
                            self.repository.current_shifts((employee_id,)).get(employee_id))


def coverage_status(days):
    expected = [day for day in days if day.schedule is None or day.schedule.events]
    if not expected:
        return "unknown" if not days else "not_applicable"
    if any(day.coverage is False for day in expected):
        return "partial"
    return "confirmed" if all(day.coverage is True for day in expected) else "unknown"


def summary_status(summary):
    if summary is None or summary.days_evaluated == 0:
        return DayStatus.NO_DATA
    if summary.anomaly_days:
        return DayStatus.ANOMALY
    if summary.observation_days:
        return DayStatus.OBSERVATION
    return DayStatus.NORMAL

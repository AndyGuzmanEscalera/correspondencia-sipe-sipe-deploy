"""Operator diagnostics: python -m app.modules.rrhh.staging_check preflight|inspect.

preflight contacts hardware ONLY when explicitly invoked. inspect only reads DB.
"""
import argparse
import json
from datetime import datetime, time, timedelta

from fastapi.encoders import jsonable_encoder
from sqlalchemy import func, select, text


def preflight(service, engine):
    # Deliberately do not use Reader.info(): that also reads the device clock.
    # No get_attendance/get_users/disable_device/write commands are called.
    with service.device_lock(engine):
        with service.reader.session() as connection:
            result = {"connected": True, "read_only": True, "device_id": service.config.device_id}
            for key, method in (("model", "get_device_name"), ("serial", "get_serialnumber"),
                                ("platform", "get_platform"), ("firmware", "get_firmware_version")):
                result[key] = getattr(connection, method)()
            connection.read_sizes()
            result["users_count"] = connection.users
            result["transport"] = service.reader.last_transport
            return result


def inspect_stored(db, source):
    from app.modules.rrhh.attendance_service import AttendanceService
    from app.modules.rrhh.models import EmployeeLink, Record, Sync
    from app.modules.rrhh.storage import record_output, sync_output
    source.check_database(db)
    config = source.config
    start = datetime.combine(config.period.start_date, time.min)
    end = datetime.combine(config.period.end_exclusive, time.min)
    job = db.scalar(select(Sync).where(Sync.device_id == config.device_id)
        .order_by(Sync.created_at.desc(), Sync.id.desc()).limit(1))
    sync = sync_output(job) if job else None
    if sync is not None:
        sync["duplicates_ignored"] = (job.downloaded_records - job.new_records
            if job.status == "completed" else None)
    total, first, last = db.execute(select(func.count(), func.min(Record.timestamp), func.max(Record.timestamp))
        .where(Record.device_id == config.device_id)).one()
    people = []
    for person in config.employees:
        link = db.get(EmployeeLink, (config.device_id, person.biometric_user_id))
        query = select(Record).where(Record.device_id == config.device_id,
            Record.user_id == person.biometric_user_id)
        count, earliest, latest = db.execute(
            select(func.count(), func.min(Record.timestamp), func.max(Record.timestamp)).where(
                Record.device_id == config.device_id, Record.user_id == person.biometric_user_id)).one()
        records = db.scalars(query.where(Record.timestamp >= start, Record.timestamp < end)
            .order_by(Record.timestamp, Record.record_id)).all()
        days = {}
        for offset in range((config.period.end_exclusive - config.period.start_date).days):
            day = config.period.start_date + timedelta(days=offset)
            days[day.isoformat()] = sum(row.timestamp.date() == day for row in records)
        report = AttendanceService(db, source).report(person.employee_id, config.contract_type_id,
            config.year, config.month)
        people.append({"employee_id": person.employee_id, "device_id": config.device_id,
            "biometric_user_id": person.biometric_user_id,
            "link_status": "unlinked" if link is None else "linked" if link.funcionario_id == person.employee_id else "linked_to_other_employee",
            "linked_employee_id": link.funcionario_id if link else None,
            "stored_range": {"total": count, "first": earliest, "last": latest},
            "daily_counts": days, "days_without_stored_marks": [key for key, value in days.items() if value == 0],
            "record_link_mismatches": [row.record_id for row in records if row.funcionario_id != person.employee_id],
            "stored_records_for_identifier": [record_output(row) for row in records],
            # Full original evidence + events/incidents/summary comes from the production service.
            "report": report.model_dump(mode="json")})
    return {"mode": "PRUEBA STAGING", "period": config.period, "latest_sync": sync,
        "device_stored_range": {"total": total, "first": first, "last": last},
        "coverage_note": "Los extremos y conteos prueban registros almacenados, no cobertura completa ni ausencia.",
        "employees": people}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "inspect"))
    args = parser.parse_args()
    try:
        from app.modules.rrhh.attendance_source import get_attendance_source
        from app.modules.rrhh.staging_source import StagingAttendanceSource
        from app.core.database import SessionLocal, engine
        source = get_attendance_source()
        if not isinstance(source, StagingAttendanceSource):
            raise ValueError("QA configuration required")
        with SessionLocal() as db:
            db.execute(text("SET TRANSACTION READ ONLY"))
            source.check_database(db)
            if args.command == "preflight":
                from app.modules.rrhh.biometric_service import BiometricService
                service = BiometricService()
                if service.config.device_id != source.config.device_id:
                    raise ValueError("Device mismatch")
                output = preflight(service, engine)
            else:
                output = inspect_stored(db, source)
        print(json.dumps(jsonable_encoder(output), ensure_ascii=False, indent=2))
    except Exception as exc:
        # Never serialize DB URLs, credentials or unfiltered driver exceptions.
        from fastapi import HTTPException
        code = exc.detail.get("code", "diagnostic_failed") if isinstance(exc, HTTPException) and isinstance(exc.detail, dict) else "diagnostic_failed"
        parser.exit(1, f"Diagnóstico no completado ({code}). Revise configuración QA, base, vínculos, turnos y conectividad; no se guardaron cambios.\n")


if __name__ == "__main__":
    main()

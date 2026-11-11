import uuid
from collections import defaultdict
from datetime import datetime, time, timedelta
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from app.modules.organization.employee import Employee
from app.modules.rrhh.models import Device, EmployeeLink, Parameters, Record
from app.modules.rrhh.reader.records import attendance_record


def ensure_device(db, config):
    db.execute(insert(Device).values(device_id=config.device_id, ip=config.ip, port=config.port)
               .on_conflict_do_update(index_elements=[Device.device_id],
                                      set_={"ip": config.ip, "port": config.port, "updated_at": func.now()}))


def employee_lookup(employees, links, match_documents=False):
    """No numeric conversion, suffix stripping, fuzzy matching or name inference."""
    candidates = defaultdict(list)
    if match_documents:
        for employee in employees:
            if employee.document_number:
                candidates[employee.document_number].append(employee.id)
    result = {key: ids[0] for key, ids in candidates.items() if len(ids) == 1}
    result.update({link.user_id: link.funcionario_id for link in links})
    return result


def persist_records(db, device_id, records, source, match_documents=False):
    if source not in {"SOURCE_API", "SOURCE_FILE"}:
        raise ValueError("Invalid source")
    employees = db.scalars(select(Employee)).all() if match_documents else []
    links = db.scalars(select(EmployeeLink).where(EmployeeLink.device_id == device_id)).all()
    lookup = employee_lookup(employees, links, match_documents)
    inserted = 0
    # Canonicalization is shared by API and file imports. Ignore supplied hashes.
    # Each batch uses ON CONFLICT, including duplicates within the same download.
    batch = []
    for item in records:
        data = dict(item)
        stamp = data["timestamp"]
        if isinstance(stamp, str):
            stamp = datetime.fromisoformat(stamp)
        if stamp.tzinfo is not None or stamp.microsecond:
            raise ValueError("Expected naive second-precision device time")
        normalized = attendance_record(SimpleNamespace(**{**data, "timestamp": stamp}), device_id)
        if not normalized["user_id"] or len(normalized["user_id"]) > 100:
            raise ValueError("Invalid biometric identifier")
        batch.append({"id": uuid.uuid4(), "record_id": normalized["record_id"],
                      "device_id": device_id, "user_id": normalized["user_id"],
                      "timestamp": stamp, "status": normalized["status"], "punch": normalized["punch"],
                      "uid": normalized["uid"], "funcionario_id": lookup.get(normalized["user_id"]),
                      "source": source})
        if len(batch) == 1000:
            inserted += _insert_batch(db, batch)
            batch = []
    if batch:
        inserted += _insert_batch(db, batch)
    return inserted


def _insert_batch(db, batch):
    return len(db.scalars(insert(Record).values(batch).on_conflict_do_nothing(
        index_elements=[Record.record_id]).returning(Record.id)).all())


def date_bounds(dates):
    return (datetime.combine(dates.start_date, time.min),
            datetime.combine(dates.end_date + timedelta(days=1), time.min))


def record_query(dates):
    start, end = date_bounds(dates)
    return select(Record).where(Record.timestamp >= start, Record.timestamp < end)


def record_output(row):
    return {"record_id": row.record_id, "device_id": row.device_id,
            "user_id": row.user_id, "funcionario_id": row.funcionario_id,
            "linked": row.funcionario_id is not None, "timestamp": row.timestamp,
            "date": row.timestamp.date(), "time": row.timestamp.time(),
            "status": row.status, "punch": row.punch, "uid": row.uid, "source": row.source}


def parameters(db):
    db.execute(insert(Parameters).values(id=1).on_conflict_do_nothing(index_elements=[Parameters.id]))
    return db.get(Parameters, 1)


def sync_output(row):
    return {key: getattr(row, key) for key in (
        "id", "device_id", "requested_start_date", "requested_end_date", "status", "created_at",
        "started_at", "finished_at", "downloaded_records", "new_records", "error_code", "error_message")}

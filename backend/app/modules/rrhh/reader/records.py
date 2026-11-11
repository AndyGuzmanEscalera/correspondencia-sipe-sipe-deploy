import hashlib
import json
from datetime import date


def attendance_record(item, device_id):
    timestamp = item.timestamp.isoformat(timespec="seconds")
    record = {
        "device_id": device_id,
        "user_id": str(item.user_id),
        "timestamp": timestamp,
        "date": item.timestamp.date().isoformat(),
        "time": item.timestamp.time().isoformat(timespec="seconds"),
        "status": getattr(item, "status", None),
        "punch": getattr(item, "punch", None),
        "uid": getattr(item, "uid", None),
    }
    # uid puede variar entre descarga y eventos; no es un ID universal de evento.
    identity = [record[k] for k in ("device_id", "user_id", "timestamp", "status", "punch")]
    record["record_id"] = hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return record


def user_record(item):
    # Lista explícita: nunca serializar __dict__, password ni plantillas.
    return {key: getattr(item, key, None)
            for key in ("uid", "user_id", "name", "privilege", "group_id", "card")}


def filter_records(records, start=None, end=None):
    lower = date.fromisoformat(start) if start else None
    upper = date.fromisoformat(end) if end else None
    if lower and upper and lower > upper:
        raise ValueError("La fecha inicial no puede ser posterior a la final.")
    return sorted(
        (r for r in records
         if (not lower or date.fromisoformat(r["date"]) >= lower)
         and (not upper or date.fromisoformat(r["date"]) <= upper)),
        key=lambda r: (r["timestamp"], r["user_id"], r["record_id"]),
    )

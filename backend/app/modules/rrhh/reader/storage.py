import csv
import json
import sqlite3
from contextlib import closing
from pathlib import Path

FIELDS = ["record_id", "device_id", "user_id", "timestamp", "date", "time", "status", "punch", "uid"]


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS attendance "
                       "(record_id TEXT PRIMARY KEY, device_id TEXT NOT NULL, payload TEXT NOT NULL)")

    def add(self, records):
        added = []
        with closing(sqlite3.connect(self.path)) as db, db:
            for record in records:
                cursor = db.execute("INSERT OR IGNORE INTO attendance VALUES (?, ?, ?)",
                                    (record["record_id"], record["device_id"],
                                     json.dumps(record, ensure_ascii=False)))
                if cursor.rowcount:
                    added.append(record)
        return added

    def all(self, device_id):
        with closing(sqlite3.connect(self.path)) as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT payload FROM attendance WHERE device_id = ?", (device_id,))]


def export_records(records, path, kind):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusivo: nunca sobrescribir un archivo existente.
    with path.open("x", encoding="utf-8-sig" if kind == "csv" else "utf-8", newline="") as handle:
        if kind == "json":
            json.dump(records, handle, ensure_ascii=False, indent=2)
        elif kind == "csv":
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            for record in records:
                # Neutralizar fórmulas al abrir identificadores en Excel.
                writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(
                    ("=", "+", "-", "@", "\t", "\r", "\n")) else v for k, v in record.items()})
        else:
            raise ValueError("Formato esperado: json o csv.")
    return path

"""Durable PostgreSQL job consumer. Run separately from uvicorn.

python -m app.modules.rrhh.worker [--once]
No hardware is contacted unless a queued job exists.
"""
import argparse
import os
import time
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from app.core.database import SessionLocal, engine
from app.modules.rrhh.biometric_service import BiometricService
from app.modules.rrhh.models import Sync
from app.modules.rrhh.storage import persist_records


def run_once(session_factory=SessionLocal, bind=engine, service_factory=BiometricService):
    # Check the durable queue before even constructing the device service.
    with session_factory() as db:
        exists = db.scalar(select(Sync.id).where(Sync.status.in_(["queued", "running"])).limit(1))
        if exists is None:
            return False
    service = service_factory()
    try:
        with service.device_lock(bind):
            with session_factory() as db:
                job = db.scalar(select(Sync).where(
                    Sync.device_id == service.config.device_id,
                    Sync.status.in_(["queued", "running"])).order_by(Sync.created_at).with_for_update())
                if job is None:
                    return False
                if job.status == "running":
                    # Lock acquired => previous worker no longer owns the physical device.
                    job.status = "failed"
                    job.error_code = "biometric_interrupted"
                    job.error_message = "El proceso anterior terminó antes de completar la sincronización. Puede solicitar otra."
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    return True
                job.status = "running"
                job.started_at = datetime.now(timezone.utc)
                job_id = job.id
                db.commit()
            try:
                rows = service.download()  # Always the full historical device log.
                with session_factory() as db:
                    job = db.get(Sync, job_id)
                    job.downloaded_records = len(rows)
                    # All records and job success commit atomically. Failed downloads import nothing.
                    job.new_records = persist_records(
                        db, job.device_id, rows, "SOURCE_API",
                        match_documents=os.getenv("BIOMETRIC_MATCH_DOCUMENT_NUMBER", "false").lower() == "true")
                    job.status = "completed"
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
            except Exception as exc:
                code, message = "biometric_sync_failed", "No se pudo completar la sincronización; no se guardaron registros."
                if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
                    code, message = exc.detail["code"], exc.detail["message"]
                with session_factory() as db:
                    job = db.get(Sync, job_id)
                    job.status, job.error_code, job.error_message = "failed", code, message
                    job.finished_at = datetime.now(timezone.utc)
                    db.commit()
            return True
    except HTTPException as exc:
        if exc.status_code == 409:
            return False
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    while True:
        try:
            processed = run_once()
        except Exception:
            # No exception dumps: connection URLs and device credentials stay private.
            print("RRHH worker: error controlado; revise configuración y disponibilidad de PostgreSQL.", flush=True)
            processed = False
        if args.once:
            return
        if not processed:
            time.sleep(5)


if __name__ == "__main__":
    main()

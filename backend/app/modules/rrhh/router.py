import os
import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.rrhh.biometric_service import BiometricService
from app.modules.rrhh.errors import problem
from app.modules.rrhh.models import Device, EmployeeLink, Parameters, Record, Sync
from app.modules.rrhh.attendance_source import AttendanceSource, get_attendance_source
from app.modules.rrhh.attendance_service import AttendanceService
from app.modules.rrhh.evaluation_models import ContractCatalog, GenerationResult, IndividualReport
from app.modules.rrhh.schemas import DateRange, FileImport, GenerateInput, LinkInput, ParametersInput
from app.modules.rrhh.storage import (ensure_device, parameters, persist_records, record_output,
                                      record_query, sync_output)

# Apache already strips /api/ for the shared backend, like /auth and /admin.
router = APIRouter(prefix="/rrhh", tags=["rrhh"])
DB = Annotated[Session, Depends(get_db)]
Read = Annotated[User, Depends(require_permission("rrhh.read"))]
Manage = Annotated[User, Depends(require_permission("rrhh.manage"))]


def date_range(start_date: date, end_date: date):
    try:
        return DateRange(start_date=start_date, end_date=end_date)
    except ValidationError:
        raise problem("invalid_date_range", "Rango inválido: ordene las fechas y use como máximo 367 días.", 422) from None


@router.get("/biometric/status")
def biometric_status(db: DB, user: Read):
    service = BiometricService()
    with service.device_lock(db.get_bind()):
        try:
            result = service.status()
        except HTTPException as exc:
            exc.detail.update(connected=False, device_id=service.config.device_id)
            raise
    ensure_device(db, service.config)
    device = db.get(Device, service.config.device_id)
    device.model, device.serial = result["model"], result["serial"]
    db.commit()
    return result


@router.get("/biometric/users")
def biometric_users(db: DB, user: Read):
    service = BiometricService()
    with service.device_lock(db.get_bind()):
        return {"device_id": service.config.device_id, "users": service.users()}


@router.post("/biometric/sync", status_code=202)
def enqueue_sync(payload: DateRange, db: DB, user: Manage):
    service = BiometricService()
    ensure_device(db, service.config)
    job = Sync(device_id=service.config.device_id, requested_start_date=payload.start_date,
               requested_end_date=payload.end_date, executed_by=user.id)
    db.add(job)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) != "uq_biometric_sync_active":
            raise
        active = db.scalar(select(Sync).where(Sync.device_id == service.config.device_id,
                                             Sync.status.in_(["queued", "running"])))
        detail = problem("biometric_sync_in_progress", "Ya existe una sincronización pendiente o en progreso.", 409)
        detail.detail["sync_id"] = str(active.id) if active else None
        raise detail from None
    return {**sync_output(job), "full_history_download": True,
            "message": "Solicitud en cola. Se descargará todo el histórico y se conservará en PostgreSQL."}


@router.get("/biometric/syncs/latest")
def latest_sync(db: DB, user: Read):
    job = db.scalar(select(Sync).order_by(Sync.created_at.desc(), Sync.id.desc()).limit(1))
    return {"sync": sync_output(job) if job is not None else None}


@router.get("/biometric/syncs/{sync_id}")
def get_sync(sync_id: uuid.UUID, db: DB, user: Read):
    job = db.get(Sync, sync_id)
    if job is None:
        raise problem("sync_not_found", "Sincronización no encontrada.", 404)
    return sync_output(job)


@router.post("/biometric/import")
def import_file(payload: FileImport, db: DB, user: Manage):
    # TXT/DAT parser remains in Flutter. Field-to-code mapping must be confirmed
    # before uploading canonical JSON; raw fields must not be guessed here.
    if db.get(Device, payload.device_id) is None:
        raise problem("device_not_found", "Registre primero el dispositivo mediante estado o sincronización.", 404)
    added = persist_records(db, payload.device_id, [row.model_dump() for row in payload.records], "SOURCE_FILE",
                            os.getenv("BIOMETRIC_MATCH_DOCUMENT_NUMBER", "false").lower() == "true")
    db.commit()
    return {"received_records": len(payload.records), "new_records": added, "source": "SOURCE_FILE"}


@router.put("/biometric/devices/{device_id}/links/{user_id}")
def link_employee(device_id: str, user_id: str, payload: LinkInput, db: DB, user: Manage):
    if not 1 <= len(user_id) <= 100 or db.get(Device, device_id) is None:
        raise problem("device_not_found", "Dispositivo o identificador inválido.", 404)
    if db.get(Employee, payload.funcionario_id) is None:
        raise problem("employee_not_found", "Funcionario no encontrado.", 404)
    db.execute(insert(EmployeeLink).values(device_id=device_id, user_id=user_id, funcionario_id=payload.funcionario_id)
               .on_conflict_do_update(index_elements=[EmployeeLink.device_id, EmployeeLink.user_id],
                                     set_={"funcionario_id": payload.funcionario_id}))
    result = db.execute(update(Record).where(Record.device_id == device_id, Record.user_id == user_id)
                        .values(funcionario_id=payload.funcionario_id))
    db.commit()
    return {"device_id": device_id, "user_id": user_id, "funcionario_id": payload.funcionario_id,
            "updated_records": result.rowcount}


@router.get("/biometric/records/summary")
def records_summary(db: DB, user: Read, dates: Annotated[DateRange, Depends(date_range)],
                    device_id: str = Query(min_length=1, max_length=100)):
    rows = record_query(dates).where(Record.device_id == device_id).subquery()
    total, linked = db.execute(select(func.count(), func.count(rows.c.funcionario_id)).select_from(rows)).one()
    unknown = select(rows.c.user_id, func.count().label("records")).where(
        rows.c.funcionario_id.is_(None)).group_by(rows.c.user_id).order_by(rows.c.user_id)
    return {"total": total, "linked": linked, "unlinked": total - linked,
            "unknown_users": [{"user_id": uid, "records": count} for uid, count in db.execute(unknown)]}


@router.get("/attendance/employees/{funcionario_id}/records")
def employee_records(funcionario_id: uuid.UUID, db: DB, user: Read,
                     dates: Annotated[DateRange, Depends(date_range)],
                     limit: int = Query(default=500, ge=1, le=5000), offset: int = Query(default=0, ge=0)):
    if db.get(Employee, funcionario_id) is None:
        raise problem("employee_not_found", "Funcionario no encontrado.", 404)
    query = record_query(dates).where(Record.funcionario_id == funcionario_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    records = db.scalars(query.order_by(Record.timestamp, Record.record_id).offset(offset).limit(limit)).all()
    return {"funcionario_id": funcionario_id, "total": total, "limit": limit, "offset": offset,
            "records": [record_output(row) for row in records]}


def parameters_output(row):
    fields = ParametersInput.model_fields
    return {**{name: getattr(row, name) for name in fields}, "unit": "seconds",
            "configured": all(getattr(row, name) is not None for name in fields),
            "updated_at": row.updated_at}


@router.get("/attendance/parameters")
def get_parameters(db: DB, user: Read):
    row = parameters(db)
    db.commit()
    return parameters_output(row)


@router.put("/attendance/parameters")
def put_parameters(payload: ParametersInput, db: DB, user: Manage):
    parameters(db)
    db.execute(update(Parameters).where(Parameters.id == 1).values(**payload.model_dump(), updated_by=user.id))
    db.commit()
    db.expire_all()
    return parameters_output(db.get(Parameters, 1))


@router.get("/attendance/contracts", response_model=ContractCatalog)
def attendance_contracts(db: DB, user: Read,
                        source: Annotated[AttendanceSource, Depends(get_attendance_source)]):
    catalog = getattr(source, "contracts", None)
    return catalog(db) if catalog is not None else ContractCatalog(
        pending_configuration=("employee_contract_assignment",))


@router.post("/attendance/generate", response_model=GenerationResult)
def generate(payload: GenerateInput, db: DB, user: Read,
             source: Annotated[AttendanceSource, Depends(get_attendance_source)]):
    return AttendanceService(db, source).generate(payload.contract_type_id, payload.year, payload.month)


@router.get("/attendance/employees/{employee_id}/report", response_model=IndividualReport)
def employee_report(employee_id: uuid.UUID, db: DB, user: Read,
                    source: Annotated[AttendanceSource, Depends(get_attendance_source)],
                    contract_type_id: uuid.UUID, year: int = Query(ge=1901, le=9998),
                    month: int = Query(ge=1, le=12)):
    return AttendanceService(db, source).report(employee_id, contract_type_id, year, month)

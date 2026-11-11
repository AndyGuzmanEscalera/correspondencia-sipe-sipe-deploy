import uuid
from datetime import date
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse, Response
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user, require_permission
from app.modules.identity.user import User
from app.modules.correspondence.insights import InsightsService
from app.modules.correspondence.report_exports import generate_export, export_filename

router = APIRouter(tags=["dashboard", "reports"])
Db = Annotated[Session, Depends(get_db)]
Reporter = Annotated[User, Depends(require_permission("reports.view"))]

def filters(kind: Literal["registered", "received", "sent", "concluded"] = "registered",
        date_from: date | None = None, date_to: date | None = None,
        document_type_id: uuid.UUID | None = None, status: Literal["ACTIVE", "CONCLUDED"] | None = None,
        unit_id: uuid.UUID | None = None, employee_id: uuid.UUID | None = None,
        search: str | None = Query(None, max_length=200)):
    return dict(kind=kind, date_from=date_from, date_to=date_to, document_type_id=document_type_id,
        status=status, unit_id=unit_id, employee_id=employee_id, search=search)

@router.get("/dashboard")
def dashboard(db: Db, user: Annotated[User, Depends(get_current_user)]):
    return InsightsService(db).dashboard(user)

@router.get("/reports/options")
def options(db: Db, user: Reporter):
    return InsightsService(db).options(user)

@router.get("/reports/export.csv")
def export(db: Db, user: Reporter, selection: Annotated[dict, Depends(filters)]):
    return StreamingResponse(InsightsService(db).export(user, **selection), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{export_filename("csv", selection)}"'})

@router.get("/reports/export.xlsx")
def export_xlsx(db: Db, user: Reporter, selection: Annotated[dict, Depends(filters)]):
    return Response(generate_export(InsightsService(db), user, "xlsx", selection),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{export_filename("xlsx", selection)}"'})

@router.get("/reports/export.pdf")
def export_pdf(db: Db, user: Reporter, selection: Annotated[dict, Depends(filters)]):
    return Response(generate_export(InsightsService(db), user, "pdf", selection), media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{export_filename("pdf", selection)}"'})

@router.get("/reports")
def reports(db: Db, user: Reporter, selection: Annotated[dict, Depends(filters)],
        page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return InsightsService(db).report(user, page, page_size, **selection)

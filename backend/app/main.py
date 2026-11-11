from fastapi import Depends, FastAPI, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.cors import configure_cors
from app.core.config import get_settings
from app.core.database import get_db
from app.core.error_middleware import SafeErrorMiddleware
from app.modules.auth.router import router as auth_router
from app.modules.correspondence.router import catalog_router as correspondence_catalog_router
from app.modules.correspondence.router import router as correspondence_router
from app.modules.correspondence.public_tracking_router import router as public_tracking_router
from app.modules.correspondence.admin_router import router as document_types_admin_router
from app.modules.identity.admin_router import router as identity_admin_router
from app.modules.organization.admin_router import (
    employees_router as admin_employees_router,
)
from app.modules.organization.admin_router import (
    positions_router as admin_positions_router,
)
from app.modules.organization.admin_router import (
    units_router as admin_units_router,
)
from app.modules.organization.router import router as organization_router
from app.modules.system.admin_router import router as system_settings_admin_router

from app.modules.notifications.router import router as notifications_router
from app.modules.correspondence.insights_router import router as insights_router

from app.modules.rrhh.router import router as rrhh_router

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(SafeErrorMiddleware)

# CORS remains outside SafeErrorMiddleware, including safe 500 responses.
configure_cors(app, settings)

app.include_router(insights_router)
app.include_router(notifications_router)
app.include_router(auth_router)
app.include_router(correspondence_catalog_router)
app.include_router(correspondence_router)
app.include_router(public_tracking_router)
app.include_router(organization_router)
app.include_router(admin_units_router)
app.include_router(admin_positions_router)
app.include_router(admin_employees_router)
app.include_router(identity_admin_router)
app.include_router(document_types_admin_router)
app.include_router(system_settings_admin_router)
app.include_router(rrhh_router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@app.get(
    "/health/database",
    tags=["health"],
    responses={
        status.HTTP_200_OK: {"description": "Database reachable"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Database unreachable"},
    },
)
def health_database(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception:
        return {"status": "error", "database": "unreachable"}

import uuid
from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.common.audit import apply_audit
from app.common.db_commit import commit_or_conflict, conflict
from app.common.schemas import ActiveToggleRequest, PaginatedResponse, paginate
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.identity.user import User
from app.modules.organization.position import Position
from app.modules.organization.position_hierarchy import PositionHierarchy


class HierarchyFields(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rank: int = Field(ge=1, le=1000000, strict=True)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError("Nombre obligatorio")
        return value.strip()


class CreateHierarchy(HierarchyFields):
    code: str = Field(min_length=1, max_length=50)

    @field_validator("code")
    @classmethod
    def clean_code(cls, value):
        if not value.strip():
            raise ValueError("Código obligatorio")
        return value.strip().upper()


class HierarchyResponse(HierarchyFields):
    id: uuid.UUID
    code: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    created_by_user_id: uuid.UUID | None
    updated_by_user_id: uuid.UUID | None

    model_config = {"from_attributes": True}


class HierarchyAdminService:
    def __init__(self, db):
        self.db = db

    def get(self, id):
        row = self.db.get(PositionHierarchy, id)
        if row is None:
            raise HTTPException(status_code=404, detail="Jerarquía no encontrada")
        return row

    def create(self, actor, body):
        if self.db.scalar(
            select(PositionHierarchy.id).where(
                func.lower(PositionHierarchy.code) == body.code.lower()
            )
        ):
            raise conflict("Ya existe una jerarquía con ese código.")
        row = PositionHierarchy(
            code=body.code, name=body.name, rank=body.rank, is_active=True
        )
        apply_audit(row, actor.id, is_create=True)
        self.db.add(row)
        commit_or_conflict(self.db, values={"code": body.code})
        self.db.refresh(row)
        return row

    def update(self, actor, id, body):
        row = self.get(id)
        row.name = body.name
        row.rank = body.rank
        apply_audit(row, actor.id)
        self.db.commit()
        self.db.refresh(row)
        return row

    def activate(self, actor, id, active):
        row = self.get(id)
        if not active and self.db.scalar(
            select(Position.id)
            .where(Position.hierarchy_id == id, Position.is_active.is_(True))
            .limit(1)
        ):
            raise HTTPException(
                status_code=409, detail="La jerarquía tiene cargos activos asignados."
            )
        row.is_active = active
        apply_audit(row, actor.id)
        self.db.commit()
        self.db.refresh(row)
        return row


router = APIRouter(prefix="/hierarchies", tags=["admin-position-hierarchies"])
DB = Annotated[Session, Depends(get_db)]
Reader = Annotated[User, Depends(require_permission("master_data.positions.read"))]
Manager = Annotated[User, Depends(require_permission("master_data.positions.manage"))]


@router.get("", response_model=PaginatedResponse[HierarchyResponse])
def list_hierarchies(
    db: DB,
    user: Reader,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=100),
    is_active: bool = True,
    include_deleted: bool = False,
    search: str | None = None,
):
    query = select(PositionHierarchy)
    if not include_deleted:
        query = query.where(PositionHierarchy.is_active == is_active)
    if search:
        query = query.where(
            PositionHierarchy.name.ilike("%" + search.strip() + "%")
            | PositionHierarchy.code.ilike("%" + search.strip() + "%")
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(PositionHierarchy.rank, PositionHierarchy.code)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return paginate(
        items=[HierarchyResponse.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{id}", response_model=HierarchyResponse)
def get_hierarchy(id: uuid.UUID, db: DB, user: Reader):
    return HierarchyAdminService(db).get(id)


@router.post("", response_model=HierarchyResponse, status_code=201)
def create_hierarchy(body: CreateHierarchy, db: DB, user: Manager):
    return HierarchyAdminService(db).create(user, body)


@router.put("/{id}", response_model=HierarchyResponse)
def update_hierarchy(id: uuid.UUID, body: HierarchyFields, db: DB, user: Manager):
    return HierarchyAdminService(db).update(user, id, body)


@router.patch("/{id}/active", response_model=HierarchyResponse)
def activate_hierarchy(id: uuid.UUID, body: ActiveToggleRequest, db: DB, user: Manager):
    return HierarchyAdminService(db).activate(user, id, body.is_active)

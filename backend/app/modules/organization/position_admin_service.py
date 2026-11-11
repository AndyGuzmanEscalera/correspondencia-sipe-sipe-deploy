import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.audit import apply_audit
from app.common.master_data_deletion import validate_deletion
from app.common.db_commit import commit_or_conflict, conflict
from app.common.normalize import normalize_code
from app.common.schemas import PaginatedResponse, paginate
from app.modules.identity.user import User
from app.modules.organization.admin_schemas import (
    CreatePositionRequest,
    PositionAdminResponse,
    UpdatePositionRequest,
)
from app.modules.organization.position import Position
from app.modules.organization.position_hierarchy import PositionHierarchy


class PositionAdminService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_positions(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        is_active: bool | None,
    ) -> PaginatedResponse[PositionAdminResponse]:
        query = select(Position)
        if is_active is not None:
            query = query.where(Position.is_active == is_active)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                Position.name.ilike(term) | Position.code.ilike(term)
            )
        total = self._db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        offset = (page - 1) * page_size
        rows = self._db.scalars(
            query.order_by(Position.name).offset(offset).limit(page_size)
        ).all()
        items = [self._to_response(row) for row in rows]
        return paginate(items=items, total=total, page=page, page_size=page_size)

    def get_position(self, position_id: uuid.UUID) -> PositionAdminResponse:
        return self._to_response(self._get_or_404(position_id))

    def create_position(
        self,
        actor: User,
        body: CreatePositionRequest,
    ) -> PositionAdminResponse:
        code = normalize_code(body.code)
        self._ensure_code_unique(code)
        self._validate_hierarchy(body.hierarchy_id)
        row = Position(
            hierarchy_id=body.hierarchy_id,
            code=code,
            name=body.name.strip(),
            description=body.description.strip() if body.description else None,
            can_view_unit_correspondence=body.can_view_unit_correspondence,
            is_active=True,
        )
        apply_audit(row, actor.id, is_create=True)
        self._db.add(row)
        commit_or_conflict(self._db, values={"code": code})
        self._db.refresh(row)
        return self._to_response(row)

    def update_position(
        self,
        actor: User,
        position_id: uuid.UUID,
        body: UpdatePositionRequest,
    ) -> PositionAdminResponse:
        row = self._get_or_404(position_id)
        code = normalize_code(body.code)
        self._ensure_code_unique(code, exclude_id=position_id)
        row.code = code
        if "hierarchy_id" in body.model_fields_set and body.hierarchy_id != row.hierarchy_id:
            self._validate_hierarchy(body.hierarchy_id)
            row.hierarchy_id = body.hierarchy_id
        row.name = body.name.strip()
        row.description = body.description.strip() if body.description else None
        if "can_view_unit_correspondence" in body.model_fields_set:
            row.can_view_unit_correspondence = body.can_view_unit_correspondence
        apply_audit(row, actor.id)
        commit_or_conflict(self._db, values={"code": code})
        self._db.refresh(row)
        return self._to_response(row)

    def set_active(
        self,
        actor: User,
        position_id: uuid.UUID,
        *,
        is_active: bool,
    ) -> PositionAdminResponse:
        row = self._get_or_404(position_id)
        if not is_active and row.is_active:
            validate_deletion(self._db, 'positions', row)
        row.is_active = is_active
        apply_audit(row, actor.id)
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def get_active_position(self, position_id: uuid.UUID) -> Position:
        row = self._db.get(Position, position_id)
        if row is None or not row.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Cargo no encontrado o inactivo",
            )
        return row

    def _get_or_404(self, position_id: uuid.UUID) -> Position:
        row = self._db.get(Position, position_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cargo no encontrado",
            )
        return row

    def _ensure_code_unique(
        self,
        code: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        query = select(Position.id).where(func.lower(Position.code) == code.lower())
        if exclude_id:
            query = query.where(Position.id != exclude_id)
        if self._db.scalar(query):
            raise conflict(f"Ya existe un cargo con el código {code}.")

    def _validate_hierarchy(self, hierarchy_id):
        if hierarchy_id is None:
            return
        row = self._db.get(PositionHierarchy, hierarchy_id)
        if row is None or not row.is_active:
            raise HTTPException(status_code=422, detail="Jerarquía no encontrada o inactiva")

    def _to_response(self, row: Position) -> PositionAdminResponse:
        hierarchy = self._db.get(PositionHierarchy, row.hierarchy_id) if row.hierarchy_id else None
        return PositionAdminResponse(
            hierarchy_id=row.hierarchy_id,
            hierarchy_code=hierarchy.code if hierarchy else None,
            hierarchy_name=hierarchy.name if hierarchy else None,
            id=row.id,
            code=row.code,
            name=row.name,
            description=row.description,
            can_view_unit_correspondence=row.can_view_unit_correspondence,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by_user_id=row.created_by_user_id,
            updated_by_user_id=row.updated_by_user_id,
        )

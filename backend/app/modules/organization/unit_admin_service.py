import math
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
    CreateOrganizationalUnitRequest,
    OrganizationalUnitAdminResponse,
    UpdateOrganizationalUnitRequest,
)
from app.modules.organization.hierarchy import would_create_cycle
from app.modules.organization.organizational_unit import OrganizationalUnit


class UnitAdminService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_units(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        is_active: bool | None,
    ) -> PaginatedResponse[OrganizationalUnitAdminResponse]:
        query = select(OrganizationalUnit)
        if is_active is not None:
            query = query.where(OrganizationalUnit.is_active == is_active)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                OrganizationalUnit.name.ilike(term)
                | OrganizationalUnit.code.ilike(term)
            )
        total = self._db.scalar(
            select(func.count()).select_from(query.subquery())
        ) or 0
        offset = (page - 1) * page_size
        rows = self._db.scalars(
            query.order_by(OrganizationalUnit.name).offset(offset).limit(page_size)
        ).all()
        parent_names = self._parent_names(rows)
        items = [self._to_response(row, parent_names.get(row.parent_id)) for row in rows]
        return paginate(items=items, total=total, page=page, page_size=page_size)

    def get_unit(self, unit_id: uuid.UUID) -> OrganizationalUnitAdminResponse:
        row = self._get_or_404(unit_id)
        parent_name = None
        if row.parent_id:
            parent = self._db.get(OrganizationalUnit, row.parent_id)
            parent_name = parent.name if parent else None
        return self._to_response(row, parent_name)

    def create_unit(
        self,
        actor: User,
        body: CreateOrganizationalUnitRequest,
    ) -> OrganizationalUnitAdminResponse:
        code = normalize_code(body.code)
        self._ensure_code_unique(code)
        self._validate_parent(body.parent_id, unit_id=None)

        row = OrganizationalUnit(
            code=code,
            name=body.name.strip(),
            description=body.description.strip() if body.description else None,
            parent_id=body.parent_id,
            is_active=True,
        )
        apply_audit(row, actor.id, is_create=True)
        self._db.add(row)
        commit_or_conflict(self._db, values={"code": code})
        self._db.refresh(row)
        return self.get_unit(row.id)

    def update_unit(
        self,
        actor: User,
        unit_id: uuid.UUID,
        body: UpdateOrganizationalUnitRequest,
    ) -> OrganizationalUnitAdminResponse:
        row = self._get_or_404(unit_id)
        code = normalize_code(body.code)
        self._ensure_code_unique(code, exclude_id=unit_id)
        self._validate_parent(body.parent_id, unit_id=unit_id)

        row.code = code
        row.name = body.name.strip()
        row.description = body.description.strip() if body.description else None
        row.parent_id = body.parent_id
        apply_audit(row, actor.id)
        commit_or_conflict(self._db, values={"code": code})
        self._db.refresh(row)
        return self.get_unit(row.id)

    def set_active(
        self,
        actor: User,
        unit_id: uuid.UUID,
        *,
        is_active: bool,
    ) -> OrganizationalUnitAdminResponse:
        row = self._get_or_404(unit_id)
        if not is_active and row.is_active:
            validate_deletion(self._db, 'units', row)
        if is_active and not row.is_active:
            self._validate_parent(row.parent_id, unit_id=row.id)
        row.is_active = is_active
        apply_audit(row, actor.id)
        self._db.commit()
        self._db.refresh(row)
        return self.get_unit(row.id)

    def _get_or_404(self, unit_id: uuid.UUID) -> OrganizationalUnit:
        row = self._db.get(OrganizationalUnit, unit_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unidad organizacional no encontrada",
            )
        return row

    def _ensure_code_unique(
        self,
        code: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        query = select(OrganizationalUnit.id).where(
            func.lower(OrganizationalUnit.code) == code.lower()
        )
        if exclude_id:
            query = query.where(OrganizationalUnit.id != exclude_id)
        if self._db.scalar(query):
            raise conflict(f"Ya existe una unidad con el código {code}.")

    def _validate_parent(
        self,
        parent_id: uuid.UUID | None,
        *,
        unit_id: uuid.UUID | None,
    ) -> None:
        if parent_id is None:
            return
        if unit_id and parent_id == unit_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Una unidad no puede ser su propia unidad padre",
            )
        parent = self._db.get(OrganizationalUnit, parent_id)
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unidad padre no encontrada",
            )
        if not parent.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La unidad padre no está activa",
            )
        if unit_id and would_create_cycle(self._db, unit_id, parent_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La asignación de unidad padre crearía un ciclo jerárquico",
            )

    def _parent_names(
        self,
        rows: list[OrganizationalUnit],
    ) -> dict[uuid.UUID | None, str | None]:
        parent_ids = {row.parent_id for row in rows if row.parent_id}
        if not parent_ids:
            return {}
        parents = self._db.scalars(
            select(OrganizationalUnit).where(OrganizationalUnit.id.in_(parent_ids))
        ).all()
        return {parent.id: parent.name for parent in parents}

    def _to_response(
        self,
        row: OrganizationalUnit,
        parent_name: str | None,
    ) -> OrganizationalUnitAdminResponse:
        return OrganizationalUnitAdminResponse(
            id=row.id,
            code=row.code,
            name=row.name,
            description=row.description,
            parent_id=row.parent_id,
            parent_name=parent_name,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by_user_id=row.created_by_user_id,
            updated_by_user_id=row.updated_by_user_id,
        )

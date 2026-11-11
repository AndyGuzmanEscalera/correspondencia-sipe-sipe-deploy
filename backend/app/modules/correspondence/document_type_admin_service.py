import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common.audit import apply_audit
from app.common.master_data_deletion import validate_deletion
from app.common.db_commit import commit_or_conflict, conflict
from app.common.normalize import normalize_code
from app.common.schemas import PaginatedResponse, paginate
from app.modules.correspondence.document_type import DocumentType
from app.modules.correspondence.document_behavior import CODE_BEHAVIORS
from app.modules.correspondence.schemas import (
    CreateDocumentTypeRequest,
    DocumentTypeAdminResponse,
    UpdateDocumentTypeRequest,
)
from app.modules.identity.user import User


class DocumentTypeAdminService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_document_types(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        is_active: bool | None,
    ) -> PaginatedResponse[DocumentTypeAdminResponse]:
        query = select(DocumentType)
        if is_active is not None:
            query = query.where(DocumentType.is_active == is_active)
        if search:
            term = f"%{search.strip()}%"
            query = query.where(
                DocumentType.name.ilike(term) | DocumentType.code.ilike(term)
            )
        total = self._db.scalar(select(func.count()).select_from(query.subquery())) or 0
        offset = (page - 1) * page_size
        rows = self._db.scalars(
            query.order_by(DocumentType.name).offset(offset).limit(page_size)
        ).all()
        items = [self._to_response(row) for row in rows]
        return paginate(items=items, total=total, page=page, page_size=page_size)

    def get_document_type(
        self, document_type_id: uuid.UUID
    ) -> DocumentTypeAdminResponse:
        return self._to_response(self._get_or_404(document_type_id))

    def create_document_type(
        self,
        actor: User,
        body: CreateDocumentTypeRequest,
    ) -> DocumentTypeAdminResponse:
        code = normalize_code(body.code)
        self._ensure_code_unique(code)
        if code in CODE_BEHAVIORS:
            raise HTTPException(
                422, "Este código está reservado por el contrato documental"
            )
        row = DocumentType(
            code=code,
            name=body.name.strip(),
            behavior=body.behavior.value,
            is_active=True,
        )
        apply_audit(row, actor.id, is_create=True)
        self._db.add(row)
        commit_or_conflict(self._db, values={"code": code})
        self._db.refresh(row)
        return self._to_response(row)

    def update_document_type(
        self,
        actor: User,
        document_type_id: uuid.UUID,
        body: UpdateDocumentTypeRequest,
    ) -> DocumentTypeAdminResponse:
        row = self._get_or_404(document_type_id)
        if body.behavior is not None and body.behavior.value != row.behavior:
            from app.modules.correspondence.correspondence import Correspondence

            if row.code.strip().upper() in CODE_BEHAVIORS or self._db.scalar(
                select(Correspondence.id)
                .where(Correspondence.document_type_id == row.id)
                .limit(1)
            ):
                raise HTTPException(
                    422,
                    "No se puede cambiar el comportamiento de un tipo estructural o utilizado",
                )
            row.behavior = body.behavior.value
        row.name = body.name.strip()
        apply_audit(row, actor.id)
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def set_active(
        self,
        actor: User,
        document_type_id: uuid.UUID,
        *,
        is_active: bool,
    ) -> DocumentTypeAdminResponse:
        row = self._get_or_404(document_type_id)
        if not is_active and row.is_active:
            validate_deletion(self._db, "document_types", row)
        row.is_active = is_active
        apply_audit(row, actor.id)
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def _ensure_code_unique(
        self,
        code: str,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        query = select(DocumentType.id).where(
            func.lower(DocumentType.code) == code.lower()
        )
        if exclude_id:
            query = query.where(DocumentType.id != exclude_id)
        if self._db.scalar(query):
            raise conflict(f"Ya existe un tipo de documento con el código {code}.")

    def _get_or_404(self, document_type_id: uuid.UUID) -> DocumentType:
        row = self._db.get(DocumentType, document_type_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tipo de documento no encontrado",
            )
        return row

    def _to_response(self, row: DocumentType) -> DocumentTypeAdminResponse:
        from app.modules.organization.position_hierarchy import PositionHierarchy

        minimum = (
            self._db.get(PositionHierarchy, row.minimum_hierarchy_id)
            if row.minimum_hierarchy_id
            else None
        )
        from app.modules.correspondence.correspondence import Correspondence

        behavior_locked = row.code.strip().upper() in CODE_BEHAVIORS or bool(
            self._db.scalar(
                select(Correspondence.id)
                .where(Correspondence.document_type_id == row.id)
                .limit(1)
            )
        )
        return DocumentTypeAdminResponse(
            behavior_locked=behavior_locked,
            profile=row.profile,
            behavior=row.behavior,
            is_operational=row.is_operational,
            creation_permission=row.creation_permission,
            minimum_hierarchy_id=row.minimum_hierarchy_id,
            minimum_hierarchy_name=minimum.name if minimum else None,
            id=row.id,
            code=row.code,
            name=row.name,
            is_active=row.is_active,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by_user_id=row.created_by_user_id,
            updated_by_user_id=row.updated_by_user_id,
        )

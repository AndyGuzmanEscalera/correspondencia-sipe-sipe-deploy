import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.common.schemas import ActiveToggleRequest, PaginatedResponse
from app.core.database import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.correspondence.document_type_admin_service import DocumentTypeAdminService
from app.modules.correspondence.schemas import (
    CreateDocumentTypeRequest,
    DocumentTypeAdminResponse,
    UpdateDocumentTypeRequest,
)
from app.modules.identity.user import User

router = APIRouter(prefix="/admin/document-types", tags=["admin-document-types"])


def _service(db: Annotated[Session, Depends(get_db)]) -> DocumentTypeAdminService:
    return DocumentTypeAdminService(db)


@router.get("", response_model=PaginatedResponse[DocumentTypeAdminResponse])
def list_document_types_admin(
    service: Annotated[DocumentTypeAdminService, Depends(_service)],
    _user: Annotated[User, Depends(require_permission("master_data.document_types.read"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = None,
    is_active: bool = True,
    include_deleted: bool = False,
) -> PaginatedResponse[DocumentTypeAdminResponse]:
    return service.list_document_types(
        page=page,
        page_size=page_size,
        search=search,
        is_active=None if include_deleted else is_active,
    )


@router.get("/{document_type_id}", response_model=DocumentTypeAdminResponse)
def get_document_type_admin(
    document_type_id: uuid.UUID,
    service: Annotated[DocumentTypeAdminService, Depends(_service)],
    _user: Annotated[User, Depends(require_permission("master_data.document_types.read"))],
) -> DocumentTypeAdminResponse:
    return service.get_document_type(document_type_id)


@router.post(
    "",
    response_model=DocumentTypeAdminResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_document_type_admin(
    body: CreateDocumentTypeRequest,
    service: Annotated[DocumentTypeAdminService, Depends(_service)],
    current_user: Annotated[User, Depends(require_permission("master_data.document_types.manage"))],
) -> DocumentTypeAdminResponse:
    return service.create_document_type(current_user, body)


@router.put("/{document_type_id}", response_model=DocumentTypeAdminResponse)
def update_document_type_admin(
    document_type_id: uuid.UUID,
    body: UpdateDocumentTypeRequest,
    service: Annotated[DocumentTypeAdminService, Depends(_service)],
    current_user: Annotated[User, Depends(require_permission("master_data.document_types.manage"))],
) -> DocumentTypeAdminResponse:
    return service.update_document_type(current_user, document_type_id, body)


@router.patch("/{document_type_id}/active", response_model=DocumentTypeAdminResponse)
def toggle_document_type_admin(
    document_type_id: uuid.UUID,
    body: ActiveToggleRequest,
    service: Annotated[DocumentTypeAdminService, Depends(_service)],
    current_user: Annotated[User, Depends(require_permission("master_data.document_types.manage"))],
) -> DocumentTypeAdminResponse:
    return service.set_active(current_user, document_type_id, is_active=body.is_active)

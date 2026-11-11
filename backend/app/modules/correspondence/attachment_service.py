"""Upload/list/download de adjuntos de correspondencia (StorageService)."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import get_storage_service
from app.modules.correspondence.correspondence import Correspondence
from app.modules.correspondence.correspondence_attachment import (
    CorrespondenceAttachment,
)
from app.modules.correspondence.schemas import CorrespondenceAttachmentResponse
from app.modules.correspondence.service import CorrespondenceService
from app.modules.correspondence.authorization import (
    ATTACHMENTS_MANAGE,
    require_operation,
)
from app.modules.identity.user import User

_FILENAME_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class CorrespondenceAttachmentService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_attachments(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool = True,
    ) -> list[CorrespondenceAttachmentResponse]:
        self._get_correspondence_or_404(correspondence_id, active_only=active_only)
        query = select(CorrespondenceAttachment).where(
            CorrespondenceAttachment.correspondence_id == correspondence_id
        )
        if active_only:
            query = query.where(
                CorrespondenceAttachment.is_active == True
            )  # noqa: E712
        rows = self._db.scalars(
            query.order_by(CorrespondenceAttachment.created_at.desc())
        ).all()
        return [self._to_response(row) for row in rows]

    async def upload_attachment(
        self,
        user: User,
        correspondence_id: uuid.UUID,
        upload: UploadFile,
        *,
        active_only: bool = True,
    ) -> CorrespondenceAttachmentResponse:
        correspondence = self._get_correspondence_or_404(
            correspondence_id,
            active_only=active_only,
            lock=True,
        )
        require_operation(self._db, user, ATTACHMENTS_MANAGE)
        CorrespondenceService(self._db).assert_current_unit_operator(
            user, correspondence
        )
        if correspondence.status != "ACTIVE":
            raise HTTPException(
                status_code=422,
                detail="No se pueden modificar adjuntos de una correspondencia concluida",
            )
        settings = get_settings()
        payload = await upload.read(settings.max_attachment_size_bytes + 1)
        if not payload:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El archivo está vacío",
            )
        if len(payload) > settings.max_attachment_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"El archivo excede el tamaño máximo "
                    f"({settings.max_attachment_size_mb} MB)"
                ),
            )

        mime_type = (upload.content_type or "application/octet-stream").lower()
        allowed = settings.attachment_allowed_mime_types_list
        if allowed and mime_type not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Tipo de archivo no permitido: {mime_type}",
            )

        original_filename = self._sanitize_filename(upload.filename or "archivo")
        attachment_id = uuid.uuid4()
        extension = Path(original_filename).suffix.lower()
        year = correspondence.created_at.year
        storage_key = (
            f"correspondence/{year}/{correspondence.id}/attachments/"
            f"{attachment_id}{extension}"
        )
        sha256 = hashlib.sha256(payload).hexdigest()

        storage = get_storage_service()
        storage.put(storage_key, payload)

        row = CorrespondenceAttachment(
            id=attachment_id,
            correspondence_id=correspondence.id,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=len(payload),
            storage_path=storage_key,
            sha256=sha256,
            is_active=True,
            created_by_user_id=user.id,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def download_attachment(
        self,
        correspondence_id: uuid.UUID,
        attachment_id: uuid.UUID,
        *,
        active_only: bool = True,
    ) -> tuple[bytes, str, str]:
        self._get_correspondence_or_404(correspondence_id, active_only=active_only)
        row = self._db.scalar(
            select(CorrespondenceAttachment).where(
                CorrespondenceAttachment.id == attachment_id,
                CorrespondenceAttachment.correspondence_id == correspondence_id,
                CorrespondenceAttachment.is_active == True,  # noqa: E712
            )
        )
        if row is None or not row.storage_path:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Adjunto no encontrado",
            )
        storage = get_storage_service()
        try:
            payload = storage.read(row.storage_path)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Archivo no encontrado en almacenamiento",
            ) from exc
        filename = row.original_filename or "adjunto"
        mime_type = row.mime_type or "application/octet-stream"
        return payload, filename, mime_type

    def deactivate_attachment(
        self,
        user: User,
        correspondence_id: uuid.UUID,
        attachment_id: uuid.UUID,
        *,
        reason: str | None = None,
        active_only: bool = True,
    ) -> CorrespondenceAttachmentResponse:
        correspondence = self._get_correspondence_or_404(
            correspondence_id, active_only=active_only, lock=True
        )
        require_operation(self._db, user, ATTACHMENTS_MANAGE)
        CorrespondenceService(self._db).assert_current_unit_operator(
            user, correspondence
        )
        if correspondence.status != "ACTIVE":
            raise HTTPException(
                status_code=422,
                detail="No se pueden modificar adjuntos de una correspondencia concluida",
            )
        row = self._db.scalar(
            select(CorrespondenceAttachment).where(
                CorrespondenceAttachment.id == attachment_id,
                CorrespondenceAttachment.correspondence_id == correspondence_id,
                CorrespondenceAttachment.is_active == True,  # noqa: E712
            )
        )
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Adjunto no encontrado",
            )
        row.is_active = False
        row.deleted_at = datetime.now(timezone.utc)
        row.deleted_by_user_id = user.id
        row.deletion_reason = reason
        self._db.commit()
        self._db.refresh(row)
        return self._to_response(row)

    def _get_correspondence_or_404(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool,
        lock: bool = False,
    ) -> Correspondence:
        query = select(Correspondence).where(Correspondence.id == correspondence_id)
        if active_only:
            query = query.where(Correspondence.is_active == True)  # noqa: E712
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        correspondence = self._db.scalar(query)
        if correspondence is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Correspondencia no encontrada",
            )
        return correspondence

    def _sanitize_filename(self, filename: str) -> str:
        name = Path(filename).name.strip()
        if not name:
            return "archivo"
        stem = Path(name).stem
        suffix = Path(name).suffix.lower()
        safe_stem = _FILENAME_SAFE.sub("_", stem).strip("._") or "archivo"
        safe_suffix = suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ""
        return f"{safe_stem[:200]}{safe_suffix}"

    def _to_response(
        self, row: CorrespondenceAttachment
    ) -> CorrespondenceAttachmentResponse:
        creator = self._db.get(User, row.created_by_user_id)
        username = creator.username if creator else None
        return CorrespondenceAttachmentResponse(
            id=row.id,
            correspondence_id=row.correspondence_id,
            original_filename=row.original_filename,
            mime_type=row.mime_type,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            is_active=row.is_active,
            created_by_user_id=row.created_by_user_id,
            created_by_username=username,
            created_at=row.created_at,
            deleted_at=row.deleted_at,
        )

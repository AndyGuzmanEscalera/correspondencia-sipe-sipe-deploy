import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.institutional_time import institutional_datetime
from app.modules.correspondence.visibility import (
    operational_visibility_filter,
    resolve_visibility_context,
    tracking_visibility_filter,
    read_visibility_filter,
)
from app.modules.correspondence.constants import (
    CORRESPONDENCE_TYPE_EXTERNAL,
    CORRESPONDENCE_TYPE_INTERNAL,
    MOVEMENT_CONCLUDED,
    MOVEMENT_CREATED,
    MOVEMENT_DERIVED,
    MOVEMENT_REOPENED,
    ROUTE_NUMBER_PREFIX,
    STATUS_ACTIVE,
    STATUS_CONCLUDED,
)
from app.modules.correspondence.correspondence import Correspondence
from app.modules.correspondence.correspondence_movement import CorrespondenceMovement
from app.modules.correspondence.correspondence_document_sequence import (
    CorrespondenceDocumentSequence,
)
from app.modules.correspondence.correspondence_route_sequence import (
    CorrespondenceRouteSequence,
)
from app.modules.correspondence.document_type import DocumentType
from app.modules.correspondence.document_behavior import DocumentBehavior, behavior_of
from app.modules.correspondence.route_sheet_policy import validate_route_sheet
from app.modules.correspondence.document_type_profiles import (
    is_internal_content_profile,
)
from app.modules.correspondence.institutional_mayor import (
    InstitutionalMayor,
    resolve_institutional_mayor,
    list_edie_first_recipients,
    resolve_edie_first_recipient,
    require_document_authority_name,
)
from app.modules.correspondence.authorization import (
    DERIVE,
    CONCLUDE,
    REOPEN,
    ATTACHMENTS_MANAGE,
    READ_PERMISSIONS,
    require_operation,
    permissions_for,
)
from app.modules.correspondence.creation_policy import (
    require_create_permission,
    require_intake_unit,
)
from app.modules.correspondence.schemas import (
    CorrespondenceDetail,
    CorrespondenceInboxCountsResponse,
    CorrespondenceListItem,
    CorrespondenceListResponse,
    CorrespondenceSentCountResponse,
    CorrespondenceMovementResponse,
    CorrespondenceLifecycleRequest,
    CreateCorrespondenceRequest,
    DeriveCorrespondenceRequest,
    DocumentTypeResponse,
    EmployeeOptionResponse,
    InstitutionalMayorResponse,
)
from app.modules.correspondence.pdf.encadenamiento_builder import (
    build_encadenamiento_context,
    movement_type_label,
    resolve_de,
    resolve_para,
)
from app.modules.correspondence.pdf.encadenamiento_pdf import (
    EncadenamientoMovementRow,
    generate_encadenamiento_pdf,
)
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.organization.position import Position


@dataclass(frozen=True)
class InstitutionalResponsible:
    user_id: uuid.UUID
    unit_id: uuid.UUID


@dataclass(frozen=True)
class InboxInstitutionalContext:
    user_id: uuid.UUID
    unit_id: uuid.UUID


@dataclass(frozen=True)
class ResolvedOrigin:
    correspondence_type: str
    origin_employee_id: uuid.UUID | None
    origin_unit_id: uuid.UUID | None
    sender_name: str | None
    sender_document: str | None
    sender_contact: str | None
    origin_description: str | None


_SENT_STATUS_VALUES = (STATUS_ACTIVE, STATUS_CONCLUDED)
_SENT_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class CorrespondenceService:
    """Business logic for correspondence milestone 1."""

    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Public API ───────────────────────────────────────────────────

    def list_document_types(self) -> list[DocumentTypeResponse]:
        rows = self._db.scalars(
            select(DocumentType)
            .where(DocumentType.is_active == True)  # noqa: E712
            .order_by(DocumentType.name)
        ).all()
        return [
            DocumentTypeResponse(
                id=row.id,
                code=row.code,
                name=row.name,
                profile=row.profile,
                behavior=behavior_of(row),
                is_operational=row.is_operational,
                creation_permission=row.creation_permission,
                minimum_hierarchy_id=row.minimum_hierarchy_id,
            )
            for row in rows
        ]

    def list_available_document_types(self, user):
        from app.modules.correspondence.document_creation_authorization import (
            authorize_document_creation,
        )

        rows = self._db.scalars(
            select(DocumentType)
            .where(
                DocumentType.is_active.is_(True), DocumentType.is_operational.is_(True)
            )
            .order_by(DocumentType.name)
        )
        result = []
        for row in rows:
            try:
                authorize_document_creation(self._db, user, row)
            except HTTPException as error:
                if error.status_code not in {403, 422}:
                    raise
                continue
            result.append(
                DocumentTypeResponse(
                    id=row.id,
                    code=row.code,
                    name=row.name,
                    profile=row.profile,
                    behavior=behavior_of(row),
                    is_operational=row.is_operational,
                    creation_permission=row.creation_permission,
                    minimum_hierarchy_id=row.minimum_hierarchy_id,
                )
            )
        return result

    def list_active_employees(self) -> list[EmployeeOptionResponse]:
        rows = self._db.scalars(
            select(Employee)
            .join(Position, Position.id == Employee.position_id)
            .join(OrganizationalUnit, OrganizationalUnit.id == Employee.unit_id)
            .where(Position.is_active.is_(True), OrganizationalUnit.is_active.is_(True))
            .where(Employee.is_active == True)  # noqa: E712
            .order_by(Employee.last_name, Employee.first_name)
        ).all()
        return [self._to_employee_option(row) for row in rows]

    def list_correspondences(
        self,
        *,
        user: User,
        page: int,
        page_size: int,
        status_filter: str | None,
        correspondence_type: str | None,
        search: str | None,
        active_only: bool = True,
    ) -> CorrespondenceListResponse:
        """Operational listing. Routers MUST pass active_only=True; audit/admin
        access to inactive records will use a dedicated endpoint later."""
        query = self._base_correspondence_query(active_only=active_only).where(
            operational_visibility_filter(self._db, user)
        )
        query = self._apply_list_filters(
            query,
            status_filter=status_filter,
            correspondence_type=correspondence_type,
            search=search,
        )
        return self._paginate_correspondences(query, page=page, page_size=page_size)

    def list_inbox(
        self,
        user: User,
        *,
        scope: str,
        page: int,
        page_size: int,
        search: str | None,
    ) -> CorrespondenceListResponse:
        """Inbox listing scoped to the authenticated user's institutional identity."""
        if scope not in {"mine", "unit"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="scope debe ser 'mine' o 'unit'",
            )

        context = resolve_visibility_context(self._db, user)
        if context is None:
            return self._empty_correspondence_page(page=page, page_size=page_size)

        query = self._base_correspondence_query(active_only=True).where(
            Correspondence.status == STATUS_ACTIVE,
            operational_visibility_filter(self._db, user),
        )
        if scope == "mine":
            query = query.where(Correspondence.current_user_id == context.user_id)
        else:
            if not context.can_view_unit_correspondence:
                raise HTTPException(
                    status_code=403,
                    detail="No tiene permiso para ver toda la correspondencia de la unidad",
                )
            query = query.where(Correspondence.current_unit_id == context.unit_id)

        query = self._apply_search_filter(query, search)
        return self._paginate_correspondences(query, page=page, page_size=page_size)

    def get_inbox_counts(self, user: User) -> CorrespondenceInboxCountsResponse:
        context = resolve_visibility_context(self._db, user)
        if context is None:
            return CorrespondenceInboxCountsResponse(mine=0, unit=0)

        base = self._base_correspondence_query(active_only=True).where(
            Correspondence.status == STATUS_ACTIVE,
            operational_visibility_filter(self._db, user),
        )
        mine = (
            self._db.scalar(
                select(func.count()).select_from(
                    base.where(
                        Correspondence.current_user_id == context.user_id
                    ).subquery()
                )
            )
            or 0
        )
        unit = (
            self._db.scalar(
                select(func.count()).select_from(
                    base.where(
                        Correspondence.current_unit_id == context.unit_id
                    ).subquery()
                )
            )
            or 0
        )
        return CorrespondenceInboxCountsResponse(
            mine=mine,
            unit=unit if context.can_view_unit_correspondence else 0,
            can_view_unit_correspondence=context.can_view_unit_correspondence,
        )

    def list_sent(
        self,
        user: User,
        *,
        page: int,
        page_size: int,
        search: str | None,
        status_filter: str | None = None,
    ) -> CorrespondenceListResponse:
        """Sent listing: correspondences initiated or derived by the session user."""
        query = self._base_sent_query(user.id, status_filter=status_filter).where(
            read_visibility_filter(self._db, user)
        )
        query = self._apply_search_filter(query, search)
        return self._paginate_sent_correspondences(
            query,
            user_id=user.id,
            page=page,
            page_size=page_size,
        )

    def get_sent_count(self, user: User) -> CorrespondenceSentCountResponse:
        query = self._base_sent_query(user.id, status_filter=None).where(
            read_visibility_filter(self._db, user)
        )
        total = self._db.scalar(select(func.count()).select_from(query.subquery())) or 0
        return CorrespondenceSentCountResponse(total=total)

    def get_correspondence(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool = True,
        user: User | None = None,
    ) -> CorrespondenceDetail:
        correspondence = self._get_correspondence_or_404(
            correspondence_id,
            active_only=active_only,
        )
        return self._to_detail(correspondence, user=user)

    def create_correspondence(
        self, user: User, body: CreateCorrespondenceRequest
    ) -> CorrespondenceDetail:
        from app.modules.correspondence.document_creation_authorization import (
            authorize_document_creation,
        )

        require_create_permission(self._db, user)
        doc_type = self._validate_document_type(body.document_type_id)
        authorize_document_creation(self._db, user, doc_type)
        return self._create_routed_correspondence(user, body)

    def _create_routed_correspondence(
        self,
        user: User,
        body: CreateCorrespondenceRequest,
    ) -> CorrespondenceDetail:
        require_create_permission(self._db, user)
        responsible = self._resolve_institutional_responsible(user)
        intake = require_intake_unit(self._db)
        # Construction is separate from public creation authorization. This also
        # keeps legacy fixture/import semantics available without exposing a route.
        doc_type = self._document_type(body.document_type_id)
        profile = behavior_of(doc_type)
        if profile == DocumentBehavior.ROUTE_SHEET:
            validate_route_sheet(body)

        origin = self._resolve_origin_fields(body, profile, user=user)
        subject = self._resolve_subject(body, profile)
        description = self._normalize_optional_text(body.description)

        recipient = None
        recipients = list_edie_first_recipients(self._db)
        if responsible.unit_id == intake.id and not recipients:
            raise HTTPException(
                status_code=422,
                detail="No hay receptores iniciales activos configurados. Configure al menos uno en Configuración → Institucional antes de registrar desde Ventanilla.",
            )
        if body.first_recipient_employee_id is not None:
            recipient = resolve_edie_first_recipient(
                self._db, body.first_recipient_employee_id
            )
            if (
                body.initial_to_unit_id is not None
                and body.initial_to_unit_id != recipient.unit_id
            ) or (
                body.initial_to_user_id is not None
                and body.initial_to_user_id != recipient.user_id
            ):
                raise HTTPException(
                    status_code=422,
                    detail="El destino no coincide con el receptor inicial seleccionado",
                )
            destination_unit, destination_user = recipient.unit_id, recipient.user_id
        else:
            if responsible.unit_id == intake.id:
                raise HTTPException(
                    status_code=422,
                    detail="Ventanilla debe seleccionar un receptor inicial configurado de Alcaldía",
                )
            if body.initial_to_unit_id is None:
                raise HTTPException(
                    status_code=422, detail="Seleccione una unidad destino"
                )
            if body.initial_to_unit_id in {item.unit_id for item in recipients}:
                raise HTTPException(
                    status_code=422,
                    detail="Para Alcaldía seleccione un receptor inicial configurado",
                )
            self._validate_destination(body.initial_to_unit_id, body.initial_to_user_id)
            destination_unit, destination_user = (
                body.initial_to_unit_id,
                body.initial_to_user_id,
            )
        mayor = (
            resolve_institutional_mayor(self._db)
            if profile
            in {DocumentBehavior.ROUTE_SHEET, DocumentBehavior.LEGACY_CHAINING}
            else None
        )

        authority_snapshot = None
        if profile in {DocumentBehavior.ROUTE_SHEET, DocumentBehavior.LEGACY_CHAINING}:
            authority_snapshot = dict(
                document_display_name=require_document_authority_name(self._db),
                para_label=require_document_authority_name(self._db),
                routing_mode="single_initial_recipient",
                initial_recipient=(
                    dict(
                        employee_id=str(recipient.employee_id),
                        user_id=str(recipient.user_id),
                        unit_id=str(recipient.unit_id),
                        employee_name=recipient.employee_name,
                        unit_name=recipient.unit_name,
                        position_name=recipient.position_name,
                    )
                    if recipient
                    else dict(
                        unit_id=str(destination_unit),
                        user_id=str(destination_user) if destination_user else None,
                        unit_name=self._unit_name(destination_unit),
                        employee_name=self._user_display_name(destination_user) or "",
                    )
                ),
            )
            if mayor:
                authority_snapshot.update(
                    employee_id=str(mayor.employee_id),
                    user_id=str(mayor.user_id),
                    unit_id=str(mayor.unit_id),
                    employee_name=mayor.employee_name,
                    position_name=mayor.position_name,
                    unit_name=mayor.unit_name,
                )

        tracking_hash = tracking_last4 = None
        if body.enable_public_tracking:
            from app.core.config import get_settings
            from app.modules.correspondence.public_tracking_service import normalize_document, document_digest
            if profile != DocumentBehavior.ROUTE_SHEET or origin.correspondence_type != "EXTERNAL" or origin.origin_employee_id:
                raise HTTPException(422, "La consulta pública corresponde a hojas de ruta de solicitantes externos.")
            document = normalize_document(body.sender_document or "")
            if not document:
                raise HTTPException(422, "Ingrese un número de documento válido para habilitar la consulta pública.")
            secret = get_settings().public_tracking_hmac_secret
            if len(secret.encode()) < 32:
                raise HTTPException(503, "La consulta pública requiere configuración institucional.")
            tracking_hash = document_digest(document, secret)
            tracking_last4 = document[-4:]

        route_year, route_sequence, route_number = self._allocate_route_number()
        doc_year, doc_sequence, doc_number = self._allocate_document_number(
            body.document_type_id
        )

        issued_at = datetime.now(timezone.utc)
        local_issued_at = institutional_datetime(issued_at)
        correspondence = Correspondence(
            route_number=route_number,
            route_sequence=route_sequence,
            route_year=route_year,
            document_sequence=doc_sequence,
            document_year=doc_year,
            document_number=doc_number,
            correspondence_type=origin.correspondence_type,
            document_type_id=body.document_type_id,
            subject=subject,
            emission_date=(
                local_issued_at.date() if profile == DocumentBehavior.ROUTE_SHEET else None
            ),
            emission_time=(
                local_issued_at.time().replace(microsecond=0)
                if profile == DocumentBehavior.ROUTE_SHEET else None
            ),
            page_count=(
                1 if profile == DocumentBehavior.ROUTE_SHEET else None
            ),
            institutional_authority_snapshot=authority_snapshot,
            reference=self._normalize_optional_text(body.reference),
            description=description,
            priority=body.priority,
            status=STATUS_ACTIVE,
            sender_name=origin.sender_name,
            sender_document=origin.sender_document,
            origin_external_document_hash=tracking_hash,
            origin_external_document_last4=tracking_last4,
            sender_contact=origin.sender_contact,
            origin_description=origin.origin_description,
            origin_unit_id=origin.origin_unit_id,
            origin_user_id=None,
            origin_employee_id=origin.origin_employee_id,
            current_unit_id=destination_unit,
            current_user_id=destination_user,
            created_by_user_id=user.id,
            is_active=True,
        )
        if doc_type.is_operational:
            from app.modules.correspondence.document_snapshot import freeze_document

            correspondence.document_snapshot = freeze_document(
                self._db, user, correspondence, doc_type, issued_at=issued_at
            )
            if profile == DocumentBehavior.ROUTE_SHEET:
                from app.modules.correspondence.pdf.route_sheet_pdf import (
                    count_route_sheet_pages,
                )

                correspondence.document_snapshot = {
                    **correspondence.document_snapshot,
                    "initial_instruction": self._normalize_optional_text(body.initial_instruction),
                }
                correspondence.page_count = count_route_sheet_pages(
                    correspondence.document_snapshot, authority_snapshot
                )
                correspondence.document_snapshot = {
                    **correspondence.document_snapshot,
                    "page_count": correspondence.page_count,
                }
        self._db.add(correspondence)
        self._db.flush()

        sequence_number = 1
        self._add_movement(
            correspondence=correspondence,
            sequence_number=sequence_number,
            movement_type=MOVEMENT_CREATED,
            from_unit_id=None,
            from_user_id=None,
            to_unit_id=responsible.unit_id,
            to_user_id=responsible.user_id,
            instruction=None,
            observation=None,
            created_by_user_id=user.id,
        )

        sequence_number += 1
        self._add_movement(
            correspondence=correspondence,
            sequence_number=sequence_number,
            movement_type=MOVEMENT_DERIVED,
            from_unit_id=responsible.unit_id,
            from_user_id=responsible.user_id,
            to_unit_id=destination_unit,
            to_user_id=destination_user,
            instruction=body.initial_instruction,
            observation=None,
            created_by_user_id=user.id,
        )

        self._db.commit()
        self._db.refresh(correspondence)
        return self._to_detail(correspondence, user=user)

    def derive_correspondence(
        self,
        user: User,
        correspondence_id: uuid.UUID,
        body: DeriveCorrespondenceRequest,
    ) -> CorrespondenceDetail:
        correspondence = self._db.scalar(
            select(Correspondence)
            .where(Correspondence.id == correspondence_id)
            .with_for_update()
        )
        if correspondence is None or not correspondence.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Correspondencia no encontrada",
            )
        self.assert_can_derive(user, correspondence)
        if correspondence.status != STATUS_ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Solo se pueden derivar trámites activos",
            )

        self._validate_destination(body.to_unit_id, body.to_user_id)
        # Subsequent routing is independent of the document profile. Intake
        # restrictions belong exclusively to create_correspondence.
        self._assert_derivation_changes_assignment(correspondence, body)

        next_sequence = self._next_movement_sequence(correspondence.id)

        self._add_movement(
            correspondence=correspondence,
            sequence_number=next_sequence,
            movement_type=MOVEMENT_DERIVED,
            from_unit_id=correspondence.current_unit_id,
            from_user_id=correspondence.current_user_id,
            to_unit_id=body.to_unit_id,
            to_user_id=body.to_user_id,
            instruction=body.instruction,
            observation=body.observation,
            created_by_user_id=user.id,
        )

        correspondence.current_unit_id = body.to_unit_id
        correspondence.current_user_id = body.to_user_id
        correspondence.updated_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(correspondence)
        return self._to_detail(correspondence, user=user)

    def conclude_correspondence(
        self,
        user: User,
        correspondence_id: uuid.UUID,
        body: CorrespondenceLifecycleRequest,
    ) -> CorrespondenceDetail:
        correspondence = self._db.scalar(
            select(Correspondence)
            .where(Correspondence.id == correspondence_id)
            .with_for_update()
        )
        if correspondence is None or not correspondence.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Correspondencia no encontrada",
            )
        require_operation(self._db, user, CONCLUDE)
        self.assert_current_unit_operator(user, correspondence)
        if correspondence.status != STATUS_ACTIVE:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Solo se pueden concluir trámites activos",
            )

        next_sequence = self._next_movement_sequence(correspondence.id)
        self._add_movement(
            correspondence=correspondence,
            sequence_number=next_sequence,
            movement_type=MOVEMENT_CONCLUDED,
            from_unit_id=correspondence.current_unit_id,
            from_user_id=correspondence.current_user_id,
            to_unit_id=None,
            to_user_id=None,
            instruction=None,
            observation=self._normalize_optional_text(body.observation),
            created_by_user_id=user.id,
        )

        correspondence.status = STATUS_CONCLUDED
        correspondence.updated_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(correspondence)
        return self._to_detail(correspondence, user=user)

    def reopen_correspondence(
        self,
        user: User,
        correspondence_id: uuid.UUID,
        body: CorrespondenceLifecycleRequest,
    ) -> CorrespondenceDetail:
        correspondence = self._db.scalar(
            select(Correspondence)
            .where(Correspondence.id == correspondence_id)
            .with_for_update()
        )
        if correspondence is None or not correspondence.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Correspondencia no encontrada",
            )
        require_operation(self._db, user, REOPEN)
        self.assert_current_unit_operator(user, correspondence)
        if correspondence.status != STATUS_CONCLUDED:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Solo se pueden reabrir trámites concluidos",
            )

        next_sequence = self._next_movement_sequence(correspondence.id)
        self._add_movement(
            correspondence=correspondence,
            sequence_number=next_sequence,
            movement_type=MOVEMENT_REOPENED,
            from_unit_id=correspondence.current_unit_id,
            from_user_id=correspondence.current_user_id,
            to_unit_id=None,
            to_user_id=None,
            instruction=None,
            observation=self._normalize_optional_text(body.observation),
            created_by_user_id=user.id,
        )

        correspondence.status = STATUS_ACTIVE
        correspondence.updated_at = datetime.now(timezone.utc)

        self._db.commit()
        self._db.refresh(correspondence)
        return self._to_detail(correspondence, user=user)

    def generate_encadenamiento_pdf_bytes(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool = True,
    ) -> tuple[bytes, str]:
        """Genera on-demand el PDF de encadenamiento desde el estado actual."""
        correspondence = self._get_correspondence_or_404(
            correspondence_id,
            active_only=active_only,
        )
        doc_type = self._document_type(correspondence.document_type_id)
        if behavior_of(doc_type) != DocumentBehavior.LEGACY_CHAINING:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "El PDF de encadenamiento solo aplica al tipo de documento "
                    "Encadenamiento"
                ),
            )
        movement_rows = self._load_encadenamiento_movements(correspondence_id)
        generated_at = datetime.now(timezone.utc)
        issued_at = correspondence.created_at
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)

        settings = get_settings()
        logo_path = settings.institutional_logo_path
        if logo_path is not None and not logo_path.is_file():
            logo_path = None

        mayor_para = self._historical_mayor_para(correspondence_id)

        para = resolve_para(
            current_unit_name=self._unit_name(correspondence.current_unit_id),
            current_user_name=self._user_display_name(correspondence.current_user_id),
            movement_rows=movement_rows,
            mayor_para=mayor_para,
        )
        de = resolve_de(
            correspondence=correspondence,
            origin_employee_name=self._employee_display_name(
                correspondence.origin_employee_id
            ),
            origin_unit_name=self._unit_name(correspondence.origin_unit_id),
            origin_position_name=self._employee_position_name(
                correspondence.origin_employee_id
            ),
        )

        try:
            context = build_encadenamiento_context(
                correspondence=correspondence,
                movement_rows=movement_rows,
                para=para,
                de=de,
                issued_at=issued_at,
                generated_at=generated_at,
                logo_path=logo_path,
            )
            pdf_bytes = generate_encadenamiento_pdf(context)
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "La generación de PDF no está disponible temporalmente. "
                    "Intente nuevamente o contacte a soporte."
                ),
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo generar el PDF de encadenamiento",
            ) from exc

        safe_route = correspondence.route_number.replace("/", "-")
        filename = f"encadenamiento_{safe_route}.pdf"
        return pdf_bytes, filename

    def list_movements(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool = True,
    ) -> list[CorrespondenceMovementResponse]:
        self._get_correspondence_or_404(correspondence_id, active_only=active_only)
        rows = self._db.scalars(
            select(CorrespondenceMovement)
            .where(CorrespondenceMovement.correspondence_id == correspondence_id)
            .order_by(CorrespondenceMovement.sequence_number)
        ).all()
        return [self._to_movement(row) for row in rows]

    # ── Internal helpers ─────────────────────────────────────────────

    def _get_correspondence_or_404(
        self,
        correspondence_id: uuid.UUID,
        *,
        active_only: bool,
    ) -> Correspondence:
        query = select(Correspondence).where(Correspondence.id == correspondence_id)
        if active_only:
            query = query.where(Correspondence.is_active == True)  # noqa: E712
        correspondence = self._db.scalar(query)
        if correspondence is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Correspondencia no encontrada",
            )
        return correspondence

    def assert_can_derive(self, user: User, correspondence: Correspondence) -> None:
        require_operation(self._db, user, DERIVE)
        codes = permissions_for(self._db, user)
        if not (codes & READ_PERMISSIONS):
            raise HTTPException(status_code=403, detail="No tiene permiso de lectura")
        context = resolve_visibility_context(self._db, user)
        if (
            context is None
            or correspondence.current_unit_id != context.unit_id
            or not (
                correspondence.current_user_id == user.id
                or (
                    correspondence.current_unit_id == context.unit_id
                    and context.can_view_unit_correspondence
                )
            )
        ):
            raise HTTPException(
                status_code=403,
                detail="No tiene alcance para derivar esta correspondencia",
            )

    def assert_current_unit_operator(
        self,
        user: User,
        correspondence: Correspondence,
    ) -> InboxInstitutionalContext:
        if not (permissions_for(self._db, user) & READ_PERMISSIONS):
            raise HTTPException(status_code=403, detail="No tiene permiso de lectura")
        context = self._resolve_inbox_context(user)
        if context is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "El usuario no tiene contexto institucional válido "
                    "para operar esta correspondencia"
                ),
            )
        if correspondence.current_unit_id != context.unit_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Solo la unidad responsable actual puede operar "
                    "esta correspondencia"
                ),
            )
        actor_context = resolve_visibility_context(self._db, user)
        if actor_context is None:
            raise HTTPException(
                status_code=403,
                detail="Su funcionario, cargo o unidad no están activos",
            )
        if (
            correspondence.current_user_id != user.id
            and not actor_context.can_view_unit_correspondence
        ):
            raise HTTPException(
                status_code=403,
                detail="No tiene alcance autorizado para operar correspondencia no asignada directamente",
            )
        assigned = (
            self._db.get(User, correspondence.current_user_id)
            if correspondence.current_user_id
            else None
        )
        if assigned and assigned.is_active and assigned.id != user.id:
            raise HTTPException(
                status_code=403,
                detail="Solo el funcionario responsable actual puede operar esta correspondencia",
            )
        return context

    def _assert_derivation_changes_assignment(
        self,
        correspondence: Correspondence,
        body: DeriveCorrespondenceRequest,
    ) -> None:
        same_unit = correspondence.current_unit_id == body.to_unit_id
        same_user = correspondence.current_user_id == body.to_user_id
        if same_unit and same_user:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La derivación debe cambiar la asignación actual",
            )

    def _resolve_inbox_context(self, user: User) -> InboxInstitutionalContext | None:
        """Institutional identity for inbox queries (session-derived, never from client)."""
        if user.employee_id is None:
            return None
        employee = self._db.get(Employee, user.employee_id)
        if employee is None or not employee.is_active:
            return None
        if employee.unit_id is None:
            return None
        unit = self._db.get(OrganizationalUnit, employee.unit_id)
        if unit is None or not unit.is_active:
            return None
        return InboxInstitutionalContext(user_id=user.id, unit_id=employee.unit_id)

    @staticmethod
    def _sent_ownership_clause(user_id: uuid.UUID):
        return tracking_visibility_filter(user_id)

    @staticmethod
    def _sent_last_sent_at_expr(user_id: uuid.UUID):
        created_part = case(
            (Correspondence.created_by_user_id == user_id, Correspondence.created_at),
            else_=_SENT_EPOCH,
        )
        derived_at = (
            select(func.max(CorrespondenceMovement.created_at))
            .where(
                CorrespondenceMovement.correspondence_id == Correspondence.id,
                CorrespondenceMovement.movement_type == MOVEMENT_DERIVED,
                CorrespondenceMovement.created_by_user_id == user_id,
            )
            .correlate(Correspondence)
            .scalar_subquery()
        )
        return func.greatest(created_part, func.coalesce(derived_at, _SENT_EPOCH))

    @classmethod
    def _base_sent_query(
        cls,
        user_id: uuid.UUID,
        *,
        status_filter: str | None,
    ):
        if status_filter is not None and status_filter not in _SENT_STATUS_VALUES:
            allowed = ", ".join(_SENT_STATUS_VALUES)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"status must be one of: {allowed}",
            )
        query = cls._base_correspondence_query(active_only=True).where(
            cls._sent_ownership_clause(user_id),
            Correspondence.status.in_(_SENT_STATUS_VALUES),
        )
        if status_filter:
            query = query.where(Correspondence.status == status_filter)
        return query

    @staticmethod
    def _base_correspondence_query(*, active_only: bool):
        query = select(Correspondence)
        if active_only:
            query = query.where(Correspondence.is_active == True)  # noqa: E712
        return query

    @staticmethod
    def _apply_search_filter(query, search: str | None):
        if not search:
            return query
        term = f"%{search.strip()}%"
        origin_employee = exists(
            select(1).where(
                Employee.id == Correspondence.origin_employee_id,
                func.concat(Employee.first_name, " ", Employee.last_name).ilike(term),
            )
        )
        destination_user = exists(
            select(1)
            .select_from(User)
            .join(Employee, Employee.id == User.employee_id)
            .where(
                User.id == Correspondence.current_user_id,
                func.concat(Employee.first_name, " ", Employee.last_name).ilike(term),
            )
        )
        destination_unit = exists(
            select(1).where(
                OrganizationalUnit.id == Correspondence.current_unit_id,
                OrganizationalUnit.name.ilike(term),
            )
        )
        origin_unit = exists(
            select(1).where(
                OrganizationalUnit.id == Correspondence.origin_unit_id,
                OrganizationalUnit.name.ilike(term),
            )
        )
        return query.where(or_(
            Correspondence.subject.ilike(term),
            Correspondence.route_number.ilike(term),
            Correspondence.cite.ilike(term),
            Correspondence.reference.ilike(term),
            Correspondence.description.ilike(term),
            Correspondence.sender_name.ilike(term),
            Correspondence.origin_description.ilike(term),
            origin_employee,
            origin_unit,
            destination_user,
            destination_unit,
        ))

    @staticmethod
    def _apply_list_filters(
        query,
        *,
        status_filter: str | None,
        correspondence_type: str | None,
        search: str | None,
    ):
        if status_filter:
            query = query.where(Correspondence.status == status_filter)
        if correspondence_type:
            query = query.where(
                Correspondence.correspondence_type == correspondence_type
            )
        return CorrespondenceService._apply_search_filter(query, search)

    def _paginate_correspondences(
        self,
        query,
        *,
        page: int,
        page_size: int,
    ) -> CorrespondenceListResponse:
        total = self._db.scalar(select(func.count()).select_from(query.subquery())) or 0
        total_pages = max(1, math.ceil(total / page_size)) if total else 0
        offset = (page - 1) * page_size

        rows = self._db.scalars(
            query.order_by(Correspondence.created_at.desc())
            .offset(offset)
            .limit(page_size)
        ).all()

        items = [self._to_list_item(row) for row in rows]
        return CorrespondenceListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    def _paginate_sent_correspondences(
        self,
        filtered_query,
        *,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> CorrespondenceListResponse:
        filtered_subq = filtered_query.subquery()
        total = self._db.scalar(select(func.count()).select_from(filtered_subq)) or 0
        total_pages = max(1, math.ceil(total / page_size)) if total else 0
        offset = (page - 1) * page_size
        last_sent_at = self._sent_last_sent_at_expr(user_id).label("last_sent_at")

        rows = self._db.execute(
            select(Correspondence, last_sent_at)
            .where(Correspondence.id.in_(select(filtered_subq.c.id)))
            .order_by(last_sent_at.desc(), Correspondence.id.desc())
            .offset(offset)
            .limit(page_size)
        ).all()

        items = [
            self._to_list_item(
                correspondence,
                last_sent_at=activity_at,
            )
            for correspondence, activity_at in rows
        ]
        return CorrespondenceListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )

    @staticmethod
    def _empty_correspondence_page(
        *,
        page: int,
        page_size: int,
    ) -> CorrespondenceListResponse:
        return CorrespondenceListResponse(
            items=[],
            page=page,
            page_size=page_size,
            total=0,
            total_pages=0,
        )

    def list_edie_first_recipients(self) -> list[InstitutionalMayorResponse]:
        return [
            InstitutionalMayorResponse(
                user_id=item.user_id,
                employee_id=item.employee_id,
                unit_id=item.unit_id,
                employee_name=item.employee_name,
                position_name=item.position_name,
                unit_name=item.unit_name,
                para_label=item.para_label,
            )
            for item in list_edie_first_recipients(self._db)
        ]

    def get_institutional_mayor(self) -> InstitutionalMayorResponse:
        mayor = self._require_institutional_mayor()
        return InstitutionalMayorResponse(
            user_id=mayor.user_id,
            employee_id=mayor.employee_id,
            unit_id=mayor.unit_id,
            employee_name=mayor.employee_name,
            position_name=mayor.position_name,
            unit_name=mayor.unit_name,
            para_label=mayor.para_label,
        )

    def _require_institutional_mayor(self) -> InstitutionalMayor:
        mayor = resolve_institutional_mayor(self._db)
        if mayor is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Debe configurar al Alcalde/Máxima Autoridad Ejecutiva antes de registrar documentos EDIE."
                ),
            )
        return mayor

    def _resolve_institutional_responsible(
        self, user: User
    ) -> InstitutionalResponsible:
        if user.employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Su cuenta de usuario no está vinculada a un funcionario. "
                    "Un administrador debe asociar su usuario a un empleado "
                    "activo en Administración → Datos básicos → Usuarios."
                ),
            )
        employee = self._db.get(Employee, user.employee_id)
        if employee is None or not employee.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El funcionario asociado no está activo",
            )
        if employee.unit_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "El funcionario no tiene una unidad organizacional asignada. "
                    "No puede registrar correspondencia."
                ),
            )
        unit = self._db.get(OrganizationalUnit, employee.unit_id)
        if unit is None or not unit.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La unidad organizacional del funcionario no está activa",
            )
        position = (
            self._db.get(Position, employee.position_id)
            if employee.position_id
            else None
        )
        if position is None or not position.is_active:
            raise HTTPException(
                status_code=422,
                detail="El cargo del funcionario asociado no está activo",
            )
        return InstitutionalResponsible(user_id=user.id, unit_id=employee.unit_id)

    def _validate_document_type(self, document_type_id: uuid.UUID) -> DocumentType:
        doc_type = self._db.get(DocumentType, document_type_id)
        if doc_type is None or not doc_type.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Tipo de documento inválido",
            )
        return doc_type

    def _resolve_origin_fields(
        self,
        body: CreateCorrespondenceRequest,
        profile: DocumentBehavior,
        *,
        user: User,
    ) -> ResolvedOrigin:
        if profile == DocumentBehavior.ROUTE_SHEET:
            validate_route_sheet(body)
            if body.origin_employee_id:
                employee = self._require_active_employee(body.origin_employee_id)
                return ResolvedOrigin(
                    correspondence_type=CORRESPONDENCE_TYPE_INTERNAL,
                    origin_employee_id=employee.id,
                    origin_unit_id=employee.unit_id,
                    sender_name=None,
                    sender_document=None,
                    sender_contact=None,
                    origin_description=None,
                )
            return ResolvedOrigin(
                correspondence_type=CORRESPONDENCE_TYPE_EXTERNAL,
                origin_employee_id=None,
                origin_unit_id=None,
                sender_name=body.sender_name.strip(),
                sender_document=self._normalize_optional_text(body.sender_document),
                sender_contact=self._normalize_optional_text(body.sender_contact),
                origin_description=self._normalize_optional_text(
                    body.origin_description
                ),
            )
        if is_internal_content_profile(profile):
            self._reject_external_sender_fields(body)
            employee = self._require_active_employee(
                body.origin_employee_id
                or (user.employee_id if profile != DocumentBehavior.REPORT else None)
            )
            return ResolvedOrigin(
                correspondence_type=CORRESPONDENCE_TYPE_INTERNAL,
                origin_employee_id=employee.id,
                origin_unit_id=employee.unit_id,
                sender_name=None,
                sender_document=None,
                sender_contact=None,
                origin_description=None,
            )

        if profile == DocumentBehavior.LEGACY_CHAINING:
            if body.correspondence_type == CORRESPONDENCE_TYPE_EXTERNAL:
                sender_name = self._normalize_optional_text(body.sender_name)
                if not sender_name:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="El nombre del remitente externo es obligatorio",
                    )
                return ResolvedOrigin(
                    correspondence_type=CORRESPONDENCE_TYPE_EXTERNAL,
                    origin_employee_id=None,
                    origin_unit_id=None,
                    sender_name=sender_name,
                    sender_document=self._normalize_optional_text(body.sender_document),
                    sender_contact=self._normalize_optional_text(body.sender_contact),
                    origin_description=self._normalize_optional_text(
                        body.origin_description
                    ),
                )

            employee = self._require_active_employee(body.origin_employee_id)
            return ResolvedOrigin(
                correspondence_type=CORRESPONDENCE_TYPE_INTERNAL,
                origin_employee_id=employee.id,
                origin_unit_id=employee.unit_id,
                sender_name=None,
                sender_document=None,
                sender_contact=None,
                origin_description=None,
            )

        # Perfil genérico: conservar comportamiento previo simplificado.
        if body.correspondence_type == CORRESPONDENCE_TYPE_EXTERNAL:
            sender_name = self._normalize_optional_text(body.sender_name)
            if not sender_name:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="sender_name es obligatorio para correspondencia externa",
                )
            return ResolvedOrigin(
                correspondence_type=CORRESPONDENCE_TYPE_EXTERNAL,
                origin_employee_id=None,
                origin_unit_id=None,
                sender_name=sender_name,
                sender_document=self._normalize_optional_text(body.sender_document),
                sender_contact=self._normalize_optional_text(body.sender_contact),
                origin_description=self._normalize_optional_text(
                    body.origin_description
                ),
            )

        employee_id = body.origin_employee_id or user.employee_id
        employee = self._require_active_employee(employee_id)
        return ResolvedOrigin(
            correspondence_type=CORRESPONDENCE_TYPE_INTERNAL,
            origin_employee_id=employee.id,
            origin_unit_id=employee.unit_id,
            sender_name=None,
            sender_document=None,
            sender_contact=None,
            origin_description=None,
        )

    def _resolve_subject(
        self,
        body: CreateCorrespondenceRequest,
        profile: DocumentBehavior,
    ) -> str:
        subject = self._normalize_optional_text(body.subject)
        description = self._normalize_optional_text(body.description)
        reference = self._normalize_optional_text(body.reference)

        if is_internal_content_profile(profile):
            if not description:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="La descripción es obligatoria para este tipo de documento",
                )
            if profile in {
                DocumentBehavior.INTERNAL_COMMUNICATION,
                DocumentBehavior.MEMORANDUM,
                DocumentBehavior.CIRCULAR,
            } and not (subject or reference):
                raise HTTPException(
                    status_code=422, detail="El asunto o la referencia es obligatorio"
                )
            if subject or reference:
                return (subject or reference)[:500]
            return description[:500]

        if profile in {DocumentBehavior.ROUTE_SHEET, DocumentBehavior.LEGACY_CHAINING}:
            if subject:
                return subject[:500]
            if reference:
                return reference[:500]
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El asunto o la referencia es obligatorio",
            )

        if not subject:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El asunto es obligatorio",
            )
        return subject[:500]

    def _reject_external_sender_fields(self, body: CreateCorrespondenceRequest) -> None:
        if any(
            self._normalize_optional_text(value)
            for value in (
                body.sender_name,
                body.sender_document,
                body.sender_contact,
                body.origin_description,
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Los campos de remitente externo no aplican a este tipo de documento",
            )
        if body.correspondence_type == CORRESPONDENCE_TYPE_EXTERNAL:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Este tipo de documento requiere origen interno (funcionario)",
            )

    def _require_active_employee(self, employee_id: uuid.UUID | None) -> Employee:
        if employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Debe seleccionar el funcionario de origen",
            )
        employee = self._db.get(Employee, employee_id)
        if employee is None or not employee.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Funcionario de origen no encontrado o inactivo",
            )
        if employee.unit_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El funcionario de origen no tiene unidad asignada",
            )
        unit = self._db.get(OrganizationalUnit, employee.unit_id)
        if unit is None or not unit.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La unidad del funcionario de origen no está activa",
            )
        return employee

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        trimmed = value.strip()
        return trimmed or None

    def _allocate_document_number(
        self,
        document_type_id: uuid.UUID,
    ) -> tuple[int, int, str]:
        year = datetime.now(timezone.utc).year
        self._db.execute(
            insert(CorrespondenceDocumentSequence)
            .values(document_type_id=document_type_id, year=year, last_sequence=0)
            .on_conflict_do_nothing(
                index_elements=["document_type_id", "year"],
            )
        )
        seq_row = self._db.scalar(
            select(CorrespondenceDocumentSequence)
            .where(
                CorrespondenceDocumentSequence.document_type_id == document_type_id,
                CorrespondenceDocumentSequence.year == year,
            )
            .with_for_update()
        )
        if seq_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo reservar secuencia del documento",
            )
        seq_row.last_sequence += 1
        sequence = seq_row.last_sequence
        return year, sequence, str(sequence)

    def _validate_destination(
        self,
        to_unit_id: uuid.UUID,
        to_user_id: uuid.UUID | None,
    ) -> None:
        unit = self._db.get(OrganizationalUnit, to_unit_id)
        if unit is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unidad destino no encontrada",
            )
        if not unit.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="La unidad destino no está activa",
            )
        if to_user_id is None:
            return

        dest_user = self._db.get(User, to_user_id)
        if dest_user is None or not dest_user.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Usuario destino no encontrado o inactivo",
            )
        if dest_user.employee_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El usuario destino no tiene funcionario asociado",
            )
        employee = self._db.get(Employee, dest_user.employee_id)
        if employee is None or not employee.is_active:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El funcionario destino no está activo",
            )
        position = (
            self._db.get(Position, employee.position_id)
            if employee.position_id
            else None
        )
        if position is None or not position.is_active:
            raise HTTPException(
                status_code=422,
                detail="El cargo del funcionario destino no está disponible",
            )
        if employee.unit_id != to_unit_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="El usuario destino no pertenece a la unidad indicada",
            )

    def _allocate_route_number(self) -> tuple[int, int, str]:
        year = datetime.now(timezone.utc).year
        self._db.execute(
            insert(CorrespondenceRouteSequence)
            .values(year=year, last_sequence=0)
            .on_conflict_do_nothing(index_elements=["year"])
        )
        seq_row = self._db.scalar(
            select(CorrespondenceRouteSequence)
            .where(CorrespondenceRouteSequence.year == year)
            .with_for_update()
        )
        if seq_row is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No se pudo reservar secuencia de hoja de ruta",
            )
        seq_row.last_sequence += 1
        sequence = seq_row.last_sequence
        route_number = f"{ROUTE_NUMBER_PREFIX}-{year}-{sequence:06d}"
        return year, sequence, route_number

    def _next_movement_sequence(self, correspondence_id: uuid.UUID) -> int:
        current_max = self._db.scalar(
            select(func.max(CorrespondenceMovement.sequence_number)).where(
                CorrespondenceMovement.correspondence_id == correspondence_id
            )
        )
        return (current_max or 0) + 1

    def _historical_mayor_para(self, correspondence_id: uuid.UUID) -> str | None:
        correspondence = self._db.get(Correspondence, correspondence_id)
        if correspondence and correspondence.institutional_authority_snapshot:
            return correspondence.institutional_authority_snapshot["para_label"]
        first = self._db.scalar(
            select(CorrespondenceMovement)
            .where(
                CorrespondenceMovement.correspondence_id == correspondence_id,
                CorrespondenceMovement.movement_type == MOVEMENT_DERIVED,
                CorrespondenceMovement.cancelled_at.is_(None),
            )
            .order_by(CorrespondenceMovement.sequence_number)
            .limit(1)
        )
        if first is None or first.to_user_id is None:
            return None
        name = self._user_display_name(first.to_user_id) or ""
        return " — ".join(
            value for value in [self._unit_name(first.to_unit_id), name] if value
        )

    def _load_encadenamiento_movements(
        self,
        correspondence_id: uuid.UUID,
    ) -> list[EncadenamientoMovementRow]:
        rows = self._db.scalars(
            select(CorrespondenceMovement)
            .where(CorrespondenceMovement.correspondence_id == correspondence_id)
            .order_by(CorrespondenceMovement.sequence_number)
        ).all()
        correspondence = self._db.get(Correspondence, correspondence_id)
        snapshot = (
            correspondence.institutional_authority_snapshot if correspondence else None
        )

        def historical_party(row, side):
            if snapshot and snapshot.get("routing_mode") == "single_initial_recipient":
                initial = snapshot.get("initial_recipient")
                if initial and (
                    (row.sequence_number == 2 and side == "to")
                    or (row.sequence_number == 3 and side == "from")
                ):
                    return initial["unit_name"], initial["employee_name"]
                return self._unit_name(
                    getattr(row, f"{side}_unit_id")
                ), self._user_display_name(getattr(row, f"{side}_user_id"))
            if snapshot and (
                (row.sequence_number == 2 and side == "to")
                or (row.sequence_number == 3 and side == "from")
            ):
                return (
                    snapshot["unit_name"],
                    snapshot.get("document_display_name") or snapshot["employee_name"],
                )
            return self._unit_name(
                getattr(row, f"{side}_unit_id")
            ), self._user_display_name(getattr(row, f"{side}_user_id"))

        return [
            EncadenamientoMovementRow(
                sequence_number=row.sequence_number,
                movement_type=row.movement_type,
                movement_type_label=movement_type_label(row.movement_type),
                from_unit=historical_party(row, "from")[0],
                from_user=historical_party(row, "from")[1],
                to_unit=historical_party(row, "to")[0],
                to_user=historical_party(row, "to")[1],
                instruction=row.instruction,
                created_at=row.created_at,
                is_cancelled=row.cancelled_at is not None,
                cancellation_reason=row.cancellation_reason,
            )
            for row in rows
        ]

    def _add_movement(
        self,
        *,
        correspondence: Correspondence,
        sequence_number: int,
        movement_type: str,
        from_unit_id: uuid.UUID | None,
        from_user_id: uuid.UUID | None,
        to_unit_id: uuid.UUID | None,
        to_user_id: uuid.UUID | None,
        instruction: str | None,
        observation: str | None,
        created_by_user_id: uuid.UUID,
    ) -> CorrespondenceMovement:
        movement = CorrespondenceMovement(
            correspondence_id=correspondence.id,
            sequence_number=sequence_number,
            movement_type=movement_type,
            from_unit_id=from_unit_id,
            from_user_id=from_user_id,
            to_unit_id=to_unit_id,
            to_user_id=to_user_id,
            instruction=instruction,
            observation=observation,
            created_by_user_id=created_by_user_id,
        )
        self._db.add(movement)
        if movement_type in {MOVEMENT_DERIVED, MOVEMENT_CONCLUDED, MOVEMENT_REOPENED}:
            from app.modules.notifications.service import notify_movement

            self._db.flush()
            notify_movement(self._db, correspondence, movement)
        return movement

    def _unit_name(self, unit_id: uuid.UUID | None) -> str | None:
        if unit_id is None:
            return None
        unit = self._db.get(OrganizationalUnit, unit_id)
        return unit.name if unit else None

    def _user_display_name(self, user_id: uuid.UUID | None) -> str | None:
        if user_id is None:
            return None
        user = self._db.get(User, user_id)
        if user is None:
            return None
        if user.employee_id:
            return self._employee_display_name(user.employee_id)
        return user.username

    def _user_is_active(self, user_id: uuid.UUID | None) -> bool | None:
        if user_id is None:
            return None
        user = self._db.get(User, user_id)
        if user is None:
            return None
        return user.is_active

    def _employee_display_name(self, employee_id: uuid.UUID | None) -> str | None:
        if employee_id is None:
            return None
        employee = self._db.get(Employee, employee_id)
        if employee is None:
            return None
        return f"{employee.first_name} {employee.last_name}".strip()

    def _employee_position_name(self, employee_id: uuid.UUID | None) -> str | None:
        if employee_id is None:
            return None
        employee = self._db.get(Employee, employee_id)
        if employee is None or employee.position_id is None:
            return None
        position = self._db.get(Position, employee.position_id)
        return position.name if position else None

    def _to_employee_option(self, employee: Employee) -> EmployeeOptionResponse:
        unit_name = self._unit_name(employee.unit_id)
        position_name = self._employee_position_name(employee.id)
        return EmployeeOptionResponse(
            id=employee.id,
            full_name=self._employee_display_name(employee.id) or "",
            unit_id=employee.unit_id,
            unit_name=unit_name,
            position_name=position_name,
            document_number=employee.document_number,
        )

    def _document_type(self, document_type_id: uuid.UUID) -> DocumentType:
        doc_type = self._db.get(DocumentType, document_type_id)
        if doc_type is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Tipo de documento no encontrado",
            )
        return doc_type

    def _to_list_item(
        self,
        correspondence: Correspondence,
        *,
        last_sent_at: datetime | None = None,
    ) -> CorrespondenceListItem:
        doc_type = self._document_type(correspondence.document_type_id)
        return CorrespondenceListItem(
            id=correspondence.id,
            route_number=correspondence.route_number,
            route_year=correspondence.route_year,
            route_sequence=correspondence.route_sequence,
            document_number=correspondence.document_number,
            document_sequence=correspondence.document_sequence,
            document_year=correspondence.document_year,
            correspondence_type=correspondence.correspondence_type,
            document_type_code=doc_type.code,
            document_behavior=DocumentBehavior(
                (correspondence.document_snapshot or {}).get(
                    "behavior", doc_type.behavior
                )
            ),
            document_type_name=doc_type.name,
            subject=correspondence.subject,
            reference=correspondence.reference,
            priority=correspondence.priority,
            status=correspondence.status,
            current_unit_id=correspondence.current_unit_id,
            current_unit_name=self._unit_name(correspondence.current_unit_id),
            current_user_id=correspondence.current_user_id,
            current_user_name=self._user_display_name(correspondence.current_user_id),
            current_user_is_active=self._user_is_active(correspondence.current_user_id),
            cite=correspondence.cite,
            registered_at=correspondence.created_at,
            last_sent_at=last_sent_at,
        )

    def _to_detail(
        self, correspondence: Correspondence, *, user: User | None = None
    ) -> CorrespondenceDetail:
        base = self._to_list_item(correspondence)
        creator = self._db.get(User, correspondence.created_by_user_id)
        can_operate = False
        if user is not None:
            try:
                self.assert_current_unit_operator(user, correspondence)
                can_operate = True
            except HTTPException:
                pass
        can_derive = False
        if (
            user is not None
            and correspondence.is_active
            and correspondence.status == STATUS_ACTIVE
        ):
            try:
                self.assert_can_derive(user, correspondence)
                can_derive = True
            except HTTPException:
                pass
        codes = permissions_for(self._db, user) if user is not None else frozenset()
        return CorrespondenceDetail(
            can_conclude=can_operate
            and correspondence.status == STATUS_ACTIVE
            and CONCLUDE in codes,
            can_reopen=can_operate
            and correspondence.status == STATUS_CONCLUDED
            and REOPEN in codes,
            can_manage_attachments=can_operate
            and correspondence.status == STATUS_ACTIVE
            and ATTACHMENTS_MANAGE in codes,
            can_operate=can_operate,
            can_derive=can_derive,
            has_document_pdf=bool(correspondence.document_snapshot),
            emission_date=correspondence.emission_date,
            emission_time=correspondence.emission_time,
            page_count=correspondence.page_count,
            para_label=(correspondence.institutional_authority_snapshot or {}).get(
                "para_label"
            ),
            **base.model_dump(),
            description=correspondence.description,
            sender_name=correspondence.sender_name,
            sender_document=correspondence.sender_document,
            sender_contact=correspondence.sender_contact,
            origin_description=correspondence.origin_description,
            origin_unit_id=correspondence.origin_unit_id,
            origin_unit_name=self._unit_name(correspondence.origin_unit_id),
            origin_user_id=correspondence.origin_user_id,
            origin_user_name=self._user_display_name(correspondence.origin_user_id),
            origin_employee_id=correspondence.origin_employee_id,
            origin_employee_name=self._employee_display_name(
                correspondence.origin_employee_id
            ),
            cite_sequence=correspondence.cite_sequence,
            cite_year=correspondence.cite_year,
            concluded_at=correspondence.concluded_at,
            reopened_at=correspondence.reopened_at,
            created_by_user_id=correspondence.created_by_user_id,
            created_by_username=creator.username if creator else "",
        )

    def _to_movement(
        self, movement: CorrespondenceMovement
    ) -> CorrespondenceMovementResponse:
        creator = self._db.get(User, movement.created_by_user_id)
        correspondence = self._db.get(Correspondence, movement.correspondence_id)
        snapshot = (
            correspondence.institutional_authority_snapshot if correspondence else None
        )
        if snapshot and snapshot.get("routing_mode") == "single_initial_recipient":
            snapshot = snapshot.get("initial_recipient")
        from_unit = self._unit_name(movement.from_unit_id)
        from_user = self._user_display_name(movement.from_user_id)
        to_unit = self._unit_name(movement.to_unit_id)
        to_user = self._user_display_name(movement.to_user_id)
        if snapshot and movement.sequence_number == 2:
            to_unit, to_user = snapshot["unit_name"], snapshot["employee_name"]
        if snapshot and movement.sequence_number == 3:
            from_unit, from_user = snapshot["unit_name"], snapshot["employee_name"]
        return CorrespondenceMovementResponse(
            id=movement.id,
            sequence_number=movement.sequence_number,
            movement_type=movement.movement_type,
            from_unit_name=from_unit,
            from_user_name=from_user,
            to_unit_name=to_unit,
            to_user_name=to_user,
            instruction=movement.instruction,
            observation=movement.observation,
            created_by_username=creator.username if creator else "",
            created_at=movement.created_at,
            cancelled_at=movement.cancelled_at,
            cancellation_reason=movement.cancellation_reason,
        )

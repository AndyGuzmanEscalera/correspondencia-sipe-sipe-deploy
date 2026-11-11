"""Scoped SQL metrics/reports. A report permission never broadens read visibility."""

import csv
import io
import uuid
from datetime import date, datetime, time, timedelta
from fastapi import HTTPException
from sqlalchemy import select, func, or_, case, exists, true
from sqlalchemy.orm import aliased, Session
from app.modules.correspondence.correspondence import Correspondence as C
from app.modules.correspondence.correspondence_movement import (
    CorrespondenceMovement as M,
)
from app.modules.correspondence.document_type import DocumentType
from app.modules.correspondence.visibility import (
    read_visibility_filter,
    resolve_visibility_context,
    tracking_visibility_filter,
    has_global_read,
)
from app.modules.organization.employee import Employee
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.identity.user import User

from app.core.institutional_time import BOLIVIA

KINDS = {"registered", "received", "sent", "concluded"}


class InsightsService:
    def __init__(self, db):
        self.db = db

    def visible(self, user):
        return select(C).where(
            C.is_active.is_(True), read_visibility_filter(self.db, user)
        )

    def dashboard(self, user):
        context = resolve_visibility_context(self.db, user)
        visible = self.visible(user)
        total, concluded = self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((C.status == "CONCLUDED", 1), else_=0)), 0),
            ).where(C.id.in_(visible.with_only_columns(C.id)))
        ).one()
        counts = {
            "mine": 0,
            "unit": None,
            "sent": 0,
            "concluded": concluded,
            "total": total,
            "can_view_unit": bool(context and context.can_view_unit_correspondence),
        }
        if context:
            counts["mine"] = (
                self.db.scalar(
                    select(func.count())
                    .select_from(C)
                    .where(
                        C.is_active.is_(True),
                        C.status == "ACTIVE",
                        C.current_user_id == user.id,
                        C.current_unit_id == context.unit_id,
                        read_visibility_filter(self.db, user),
                    )
                )
                or 0
            )
            if context.can_view_unit_correspondence:
                counts["unit"] = (
                    self.db.scalar(
                        select(func.count())
                        .select_from(C)
                        .where(
                            C.is_active.is_(True),
                            C.status == "ACTIVE",
                            C.current_unit_id == context.unit_id,
                        )
                    )
                    or 0
                )
        counts["sent"] = (
            self.db.scalar(
                select(func.count())
                .select_from(C)
                .where(
                    C.is_active.is_(True),
                    tracking_visibility_filter(user.id),
                    read_visibility_filter(self.db, user),
                )
            )
            or 0
        )
        events = self.db.execute(
            select(
                M.id,
                M.correspondence_id,
                M.movement_type,
                M.created_at,
                C.route_number,
                C.subject,
                M.to_user_id,
                M.to_unit_id,
            )
            .join(C, C.id == M.correspondence_id)
            .where(
                C.is_active.is_(True),
                read_visibility_filter(self.db, user),
                M.cancelled_at.is_(None),
                M.movement_type.in_(["DERIVED", "CONCLUDED", "REOPENED"]),
            )
            .order_by(M.created_at.desc(), M.id.desc())
            .limit(10)
        )
        counts["activity"] = [
            {
                "id": r.id,
                "correspondence_id": r.correspondence_id,
                "type": (
                    "RECEIVED"
                    if r.movement_type == "DERIVED"
                    and (
                        r.to_user_id == user.id
                        or (
                            context
                            and context.can_view_unit_correspondence
                            and r.to_unit_id == context.unit_id
                        )
                    )
                    else r.movement_type
                ),
                "created_at": r.created_at,
                "route_number": r.route_number,
                "subject": r.subject,
            }
            for r in events
        ]
        return counts

    def options(self, user):
        context = resolve_visibility_context(self.db, user)
        types = self.db.execute(
            select(DocumentType.id, DocumentType.name)
            .where(
                or_(
                    DocumentType.is_active.is_(True),
                    DocumentType.id.in_(
                        self.visible(user).with_only_columns(C.document_type_id)
                    ),
                )
            )
            .order_by(DocumentType.name)
        )
        units = []
        employees = []
        global_read = has_global_read(self.db, user)
        if global_read:
            units = [
                {"id": row.id, "name": row.name}
                for row in self.db.scalars(
                    select(OrganizationalUnit).order_by(OrganizationalUnit.name)
                )
            ]
            employees = [
                {"id": e.id, "name": f"{e.first_name} {e.last_name}"}
                for e in self.db.scalars(
                    select(Employee).order_by(Employee.last_name, Employee.id)
                )
            ]
        elif context:
            if context.can_view_unit_correspondence:
                units = [
                    {"id": row.id, "name": row.name}
                    for row in self.db.scalars(
                        select(OrganizationalUnit).where(
                            OrganizationalUnit.id == context.unit_id
                        )
                    )
                ]
                employee_query = select(Employee).where(
                    Employee.unit_id == context.unit_id, Employee.is_active.is_(True)
                )
            else:
                employee_query = select(Employee).where(
                    Employee.id == user.employee_id, Employee.is_active.is_(True)
                )
            employees = [
                {"id": e.id, "name": f"{e.first_name} {e.last_name}"}
                for e in self.db.scalars(
                    employee_query.order_by(Employee.last_name, Employee.id)
                )
            ]
        return {
            "types": [{"id": row.id, "name": row.name} for row in types],
            "units": units,
            "employees": employees,
            "can_view_unit": bool(
                global_read or (context and context.can_view_unit_correspondence)
            ),
            "can_view_all": global_read,
        }

    def query(
        self,
        user,
        *,
        kind="registered",
        date_from=None,
        date_to=None,
        document_type_id=None,
        status=None,
        unit_id=None,
        employee_id=None,
        search=None,
    ):
        if kind not in KINDS:
            raise HTTPException(422, "Tipo de reporte inválido")
        if date_from and date_to and date_from > date_to:
            raise HTTPException(
                422, "La fecha desde no puede ser posterior a la fecha hasta"
            )
        context = resolve_visibility_context(self.db, user)
        global_read = has_global_read(self.db, user)
        q = self.visible(user)
        event_date = C.created_at
        if kind == "received":
            incoming = true() if global_read else M.to_user_id == user.id
            if context and context.can_view_unit_correspondence:
                incoming = or_(incoming, M.to_unit_id == context.unit_id)
            receipt = (
                select(func.max(M.created_at))
                .where(
                    M.correspondence_id == C.id,
                    M.movement_type == "DERIVED",
                    M.cancelled_at.is_(None),
                    incoming,
                )
                .correlate(C)
                .scalar_subquery()
            )
            q = q.where(receipt.is_not(None))
            event_date = receipt
        elif kind == "sent":
            q = q.where(tracking_visibility_filter(user.id))
            last_sent = (
                select(func.max(M.created_at))
                .where(
                    M.correspondence_id == C.id,
                    M.movement_type == "DERIVED",
                    M.cancelled_at.is_(None),
                    M.created_by_user_id == user.id,
                )
                .correlate(C)
                .scalar_subquery()
            )
            event_date = func.coalesce(last_sent, C.created_at)
        elif kind == "concluded":
            q = q.where(C.status == "CONCLUDED")
            last_close = (
                select(func.max(M.created_at))
                .where(
                    M.correspondence_id == C.id,
                    M.movement_type == "CONCLUDED",
                    M.cancelled_at.is_(None),
                )
                .correlate(C)
                .scalar_subquery()
            )
            event_date = func.coalesce(last_close, C.concluded_at, C.updated_at)
        if date_from:
            q = q.where(event_date >= datetime.combine(date_from, time.min, BOLIVIA))
        if date_to:
            q = q.where(
                event_date
                < datetime.combine(date_to + timedelta(days=1), time.min, BOLIVIA)
            )
        if document_type_id:
            q = q.where(C.document_type_id == document_type_id)
        if status:
            q = q.where(C.status == status)
        if unit_id:
            if not global_read and (
                not context
                or not context.can_view_unit_correspondence
                or unit_id != context.unit_id
            ):
                raise HTTPException(403, "Su alcance no permite filtrar esa unidad")
            q = q.where(C.current_unit_id == unit_id)
        if employee_id:
            allowed = select(Employee.id).where(Employee.id == employee_id)
            if not global_read:
                if context and context.can_view_unit_correspondence:
                    allowed = allowed.where(Employee.unit_id == context.unit_id)
                else:
                    allowed = allowed.where(Employee.id == user.employee_id)
            if self.db.scalar(allowed) is None:
                raise HTTPException(403, "Funcionario fuera de su alcance")
            assigned = select(User.id).where(User.employee_id == employee_id)
            q = q.where(C.current_user_id.in_(assigned))
        if search and search.strip():
            # Treat wildcards as literal search text rather than broadening a filter.
            pattern = (
                "%"
                + search.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                + "%"
            )
            q = q.where(
                or_(
                    *(
                        col.ilike(pattern, escape="\\")
                        for col in [C.route_number, C.subject, C.reference, C.cite]
                    )
                )
            )
        return q, event_date

    def rows_query(self, q, event_date):
        unit = aliased(OrganizationalUnit)
        origin = aliased(OrganizationalUnit)
        responsible = aliased(User)
        employee = aliased(Employee)
        return (
            select(
                C.id,
                C.route_number,
                event_date.label("date"),
                DocumentType.name.label("document_type"),
                C.subject,
                C.reference,
                C.status,
                func.coalesce(
                    C.sender_name, origin.name, C.origin_description, "—"
                ).label("origin"),
                unit.name.label("destination"),
                func.coalesce(
                    employee.first_name + " " + employee.last_name, responsible.username
                ).label("employee"),
            )
            .join(DocumentType, DocumentType.id == C.document_type_id)
            .outerjoin(unit, unit.id == C.current_unit_id)
            .outerjoin(origin, origin.id == C.origin_unit_id)
            .outerjoin(responsible, responsible.id == C.current_user_id)
            .outerjoin(employee, employee.id == responsible.employee_id)
            .where(C.id.in_(q.with_only_columns(C.id)))
            .order_by(event_date.desc(), C.id.desc())
        )

    def report(self, user, page, page_size, **filters):
        q, event_date = self.query(user, **filters)
        ids = q.with_only_columns(C.id)
        total, pending, concluded = self.db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((C.status == "ACTIVE", 1), else_=0)), 0),
                func.coalesce(func.sum(case((C.status == "CONCLUDED", 1), else_=0)), 0),
            ).where(C.id.in_(ids))
        ).one()
        type_counts = self.db.execute(
            select(DocumentType.name, func.count())
            .join(C, C.document_type_id == DocumentType.id)
            .where(C.id.in_(ids))
            .group_by(DocumentType.id, DocumentType.name)
            .order_by(DocumentType.name)
        )
        units = []
        context = resolve_visibility_context(self.db, user)
        if has_global_read(self.db, user) or (
            context and context.can_view_unit_correspondence
        ):
            units = [
                {"name": name or "Sin unidad", "total": count}
                for name, count in self.db.execute(
                    select(OrganizationalUnit.name, func.count())
                    .select_from(C)
                    .outerjoin(
                        OrganizationalUnit, OrganizationalUnit.id == C.current_unit_id
                    )
                    .where(C.id.in_(ids))
                    .group_by(OrganizationalUnit.id, OrganizationalUnit.name)
                    .order_by(OrganizationalUnit.name)
                )
            ]
        rows = self.db.execute(
            self.rows_query(q, event_date)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            "items": [dict(row._mapping) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size,
            "summary": {
                "total": total,
                "pending": pending,
                "concluded": concluded,
                "by_type": [
                    {"name": name, "total": count} for name, count in type_counts
                ],
                "by_unit": units,
            },
        }

    def export(self, user, **filters):
        q, event_date = self.query(user, **filters)
        # Query/authorization evaluated before starting the streaming response.
        bind = self.db.get_bind()

        def safe(value):
            text = str(value or "")
            return (
                "'" + text
                if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r"))
                else text
            )

        def stream():
            buf = io.StringIO()
            writer = csv.writer(buf)
            yield "\ufeff"
            writer.writerow(
                [
                    "Hoja de ruta",
                    "Fecha (Bolivia)",
                    "Tipo",
                    "Asunto",
                    "Referencia",
                    "Origen",
                    "Destino actual",
                    "Funcionario",
                    "Estado",
                ]
            )
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
            # A request dependency can close before StreamingResponse consumes rows.
            with Session(bind) as session:
                service = InsightsService(session)
                scoped, timestamp = service.query(user, **filters)
                rows = session.execute(
                    service.rows_query(scoped, timestamp).execution_options(
                        yield_per=200
                    )
                )
                for row in rows:
                    d = row.date.astimezone(BOLIVIA).isoformat() if row.date else ""
                    writer.writerow(
                        [
                            safe(v)
                            for v in [
                                row.route_number,
                                d,
                                row.document_type,
                                row.subject,
                                row.reference,
                                row.origin,
                                row.destination,
                                row.employee,
                                row.status,
                            ]
                        ]
                    )
                    yield buf.getvalue()
                    buf.seek(0)
                    buf.truncate(0)

        return stream()

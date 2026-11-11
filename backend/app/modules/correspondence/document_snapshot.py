"""Immutable semantic snapshot of newly issued operational documents."""

from datetime import datetime, timezone
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.position import Position
from app.modules.organization.position_hierarchy import PositionHierarchy
from app.modules.organization.organizational_unit import OrganizationalUnit


def party(db, employee_id=None, unit_id=None, user_id=None):
    user = db.get(User, user_id) if user_id else None
    employee_id = employee_id or (user.employee_id if user else None)
    employee = db.get(Employee, employee_id) if employee_id else None
    position = (
        db.get(Position, employee.position_id)
        if employee and employee.position_id
        else None
    )
    hierarchy = (
        db.get(PositionHierarchy, position.hierarchy_id)
        if position and position.hierarchy_id
        else None
    )
    unit_id = unit_id or (employee.unit_id if employee else None)
    unit = db.get(OrganizationalUnit, unit_id) if unit_id else None
    return {
        "employee_id": str(employee.id) if employee else None,
        "employee_name": (
            f"{employee.first_name} {employee.last_name}" if employee else ""
        ),
        "user_id": str(user_id) if user_id else None,
        "position_name": position.name if position else "",
        "unit_name": unit.name if unit else "",
        "hierarchy": (
            {
                "id": str(hierarchy.id),
                "code": hierarchy.code,
                "name": hierarchy.name,
                "rank": hierarchy.rank,
            }
            if hierarchy
            else None
        ),
    }


def freeze_document(db, actor, row, document_type, *, issued_at=None):
    return {
        "version": 1,
        "template": "institutional-document-v1",
        "document_type_id": str(document_type.id),
        "code": document_type.code,
        "name": document_type.name,
        "profile": document_type.profile,
        "behavior": document_type.behavior,
        "emission_date": row.emission_date.isoformat() if row.emission_date else None,
        "emission_time": row.emission_time.isoformat() if row.emission_time else None,
        "page_count": row.page_count,
        "issued_at": (issued_at or datetime.now(timezone.utc)).isoformat(),
        "document_number": row.document_number,
        "document_year": row.document_year,
        "route_number": row.route_number,
        "subject": row.subject,
        "reference": row.reference,
        "content": row.description,
        "priority": row.priority,
        "actor": party(db, employee_id=actor.employee_id, user_id=actor.id),
        "origin": (
            party(db, employee_id=row.origin_employee_id, unit_id=row.origin_unit_id)
            if row.origin_employee_id
            else {"employee_name": row.sender_name, "external": True}
        ),
        "destination": party(
            db, unit_id=row.current_unit_id, user_id=row.current_user_id
        ),
    }

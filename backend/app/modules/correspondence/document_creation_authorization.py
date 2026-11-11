"""Actor-based authorization for new operational documents, independent of origin."""

from fastapi import HTTPException
from app.modules.identity.rbac_service import RbacService
from app.modules.organization.employee import Employee
from app.modules.organization.position import Position
from app.modules.organization.position_hierarchy import PositionHierarchy
from app.modules.organization.organizational_unit import OrganizationalUnit
from app.modules.correspondence.document_type_profiles import (
    STRUCTURAL_DOCUMENTS,
    normalize_document_type_code,
)


def authorize_document_creation(db, actor, document_type):
    code = normalize_document_type_code(document_type.code)
    if (
        not document_type.is_active
        or not document_type.is_operational
        or code not in STRUCTURAL_DOCUMENTS
    ):
        raise HTTPException(
            status_code=422,
            detail="Este tipo documental solo está disponible para consulta histórica",
        )
    expected = STRUCTURAL_DOCUMENTS[code]
    if (
        document_type.behavior != expected[1].value
        or document_type.creation_permission != expected[2]
    ):
        raise HTTPException(
            status_code=422,
            detail="La configuración estructural del tipo documental no es válida",
        )
    if not RbacService(db).has_permission(actor.id, document_type.creation_permission):
        raise HTTPException(
            status_code=403, detail="No tiene permiso para crear este tipo documental"
        )
    employee = db.get(Employee, actor.employee_id) if actor.employee_id else None
    position = (
        db.get(Position, employee.position_id)
        if employee and employee.position_id
        else None
    )
    unit = (
        db.get(OrganizationalUnit, employee.unit_id)
        if employee and employee.unit_id
        else None
    )
    if (
        not actor.is_active
        or not employee
        or not employee.is_active
        or not position
        or not position.is_active
        or not unit
        or not unit.is_active
    ):
        raise HTTPException(
            status_code=422,
            detail="La creación requiere funcionario, cargo y unidad activos",
        )
    hierarchy = (
        db.get(PositionHierarchy, position.hierarchy_id)
        if position.hierarchy_id
        else None
    )
    if document_type.minimum_hierarchy_id:
        minimum = db.get(PositionHierarchy, document_type.minimum_hierarchy_id)
        if not minimum or not minimum.is_active:
            raise HTTPException(
                status_code=422,
                detail="La jerarquía mínima del tipo documental no está disponible",
            )
        if not hierarchy or not hierarchy.is_active or hierarchy.rank < minimum.rank:
            raise HTTPException(
                status_code=403,
                detail="La jerarquía del funcionario autenticado no permite crear este documento",
            )
    elif expected[3] is not None:
        raise HTTPException(
            status_code=422,
            detail="Falta la jerarquía mínima estructural del tipo documental",
        )
    # Unrestricted profiles accept transitional cargos without hierarchy, but not
    # a hierarchy that has explicitly been disabled.
    elif hierarchy and not hierarchy.is_active:
        raise HTTPException(
            status_code=403,
            detail="La jerarquía del funcionario autenticado está inactiva",
        )
    return employee, position, hierarchy

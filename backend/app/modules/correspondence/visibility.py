"""Central read policy: explicit scopes, independent of roles and legacy flags."""

from dataclasses import dataclass
import uuid
from fastapi import HTTPException
from sqlalchemy import exists, false, or_, select, true
from sqlalchemy.orm import Session
from app.modules.correspondence.correspondence import Correspondence
from app.modules.correspondence.correspondence_movement import CorrespondenceMovement
from app.modules.correspondence.constants import MOVEMENT_DERIVED
from app.modules.correspondence.authorization import (
    READ_OWN,
    READ_UNIT,
    READ_ALL,
    permissions_for,
)
from app.modules.identity.user import User
from app.modules.organization.employee import Employee
from app.modules.organization.position import Position
from app.modules.organization.organizational_unit import OrganizationalUnit


@dataclass(frozen=True)
class VisibilityContext:
    user_id: uuid.UUID
    unit_id: uuid.UUID
    can_view_unit_correspondence: bool
    can_read_own: bool = False
    can_read_all: bool = False


def has_global_read(db, user):
    return READ_ALL in permissions_for(db, user)


def resolve_visibility_context(db: Session, user: User) -> VisibilityContext | None:
    row = db.execute(
        select(Employee.unit_id)
        .join(Position, Position.id == Employee.position_id)
        .join(OrganizationalUnit, OrganizationalUnit.id == Employee.unit_id)
        .where(
            Employee.id == user.employee_id,
            Employee.is_active.is_(True),
            Position.is_active.is_(True),
            OrganizationalUnit.is_active.is_(True),
        )
    ).first()
    if not user.is_active or row is None:
        return None
    codes = permissions_for(db, user)
    return VisibilityContext(
        user.id,
        row.unit_id,
        bool({READ_UNIT, READ_ALL} & codes),
        READ_OWN in codes,
        READ_ALL in codes,
    )


def operational_visibility_filter(db: Session, user: User):
    if has_global_read(db, user):
        return true()
    context = resolve_visibility_context(db, user)
    if context is None:
        return false()
    unit = Correspondence.current_unit_id == context.unit_id
    if context.can_view_unit_correspondence:
        return unit
    return (
        unit & (Correspondence.current_user_id == context.user_id)
        if context.can_read_own
        else false()
    )


def tracking_visibility_filter(user_id: uuid.UUID):
    """Personal relationship; callers must also apply an authorized read scope."""
    derived_by_me = exists(
        select(1)
        .where(
            CorrespondenceMovement.correspondence_id == Correspondence.id,
            CorrespondenceMovement.movement_type == MOVEMENT_DERIVED,
            CorrespondenceMovement.created_by_user_id == user_id,
        )
        .correlate(Correspondence)
    )
    return or_(Correspondence.created_by_user_id == user_id, derived_by_me)


def read_visibility_filter(db: Session, user: User):
    codes = permissions_for(db, user)
    if READ_ALL in codes:
        return true()
    personal = tracking_visibility_filter(user.id) if READ_OWN in codes else false()
    return or_(operational_visibility_filter(db, user), personal)


def require_visible_correspondence(
    db: Session, user: User, correspondence_id: uuid.UUID
):
    correspondence = db.scalar(
        select(Correspondence).where(
            Correspondence.id == correspondence_id,
            Correspondence.is_active.is_(True),
            read_visibility_filter(db, user),
        )
    )
    if correspondence is None:
        raise HTTPException(status_code=404, detail="Correspondencia no encontrada")
    return correspondence

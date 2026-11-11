import uuid

from sqlalchemy.orm import Session

from app.modules.organization.organizational_unit import OrganizationalUnit


def would_create_cycle(
    db: Session,
    unit_id: uuid.UUID,
    new_parent_id: uuid.UUID | None,
) -> bool:
    if new_parent_id is None:
        return False
    if unit_id == new_parent_id:
        return True

    current: uuid.UUID | None = new_parent_id
    visited: set[uuid.UUID] = set()
    while current is not None:
        if current == unit_id:
            return True
        if current in visited:
            return False
        visited.add(current)
        parent = db.get(OrganizationalUnit, current)
        current = parent.parent_id if parent else None
    return False

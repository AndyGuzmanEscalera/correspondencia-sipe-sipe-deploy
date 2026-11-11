import uuid
from datetime import datetime, timezone


def apply_audit(
    entity: object,
    actor_user_id: uuid.UUID,
    *,
    is_create: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    if hasattr(entity, "updated_by_user_id"):
        entity.updated_by_user_id = actor_user_id  # type: ignore[attr-defined]
    if hasattr(entity, "updated_at"):
        entity.updated_at = now  # type: ignore[attr-defined]
    if is_create and hasattr(entity, "created_by_user_id"):
        entity.created_by_user_id = actor_user_id  # type: ignore[attr-defined]

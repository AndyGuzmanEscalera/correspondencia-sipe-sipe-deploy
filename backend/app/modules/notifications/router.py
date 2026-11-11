import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.modules.auth.dependencies import get_current_user
from app.modules.identity.user import User
from app.modules.notifications.service import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])
Db = Annotated[Session, Depends(get_db)]
Actor = Annotated[User, Depends(get_current_user)]

@router.get("")
def list_notifications(db: Db, user: Actor, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    return NotificationService(db).list(user, page, page_size)

@router.get("/unread-count")
def unread_count(db: Db, user: Actor):
    return {"unread": NotificationService(db).unread_count(user)}

@router.post("/read-all")
def read_all(db: Db, user: Actor):
    return NotificationService(db).read_all(user)

@router.post("/{notification_id}/read")
def read_one(notification_id: uuid.UUID, db: Db, user: Actor):
    return NotificationService(db).read(user, notification_id)

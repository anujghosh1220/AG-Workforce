import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import Notification, NotificationType
from app.schemas import NotificationOut

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=Page[NotificationOut])
def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    unread_only: bool = False,
    type_: NotificationType | None = Query(None, alias="type"),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    uid = auth.user.id
    query = select(Notification).where(
        Notification.workspace_id == wid,
        or_(Notification.user_id == uid, Notification.user_id.is_(None)),
    )
    if unread_only:
        query = query.where(Notification.is_read == False)  # noqa: E712
    if type_:
        query = query.where(Notification.type == type_)
    query = query.order_by(Notification.created_at.desc())

    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([NotificationOut.model_validate(n) for n in items], total, page, page_size)


@router.get("/unread-count")
def unread_count(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    count = db.scalar(
        select(func.count(Notification.id)).where(
            Notification.workspace_id == auth.workspace.id,
            or_(Notification.user_id == auth.user.id, Notification.user_id.is_(None)),
            Notification.is_read == False,  # noqa: E712
        )
    )
    return {"count": count or 0}


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_read(notification_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    note = db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.workspace_id == auth.workspace.id,
            or_(Notification.user_id == auth.user.id, Notification.user_id.is_(None)),
        )
    )
    if note is None:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Notification not found")
    note.is_read = True
    db.commit()
    db.refresh(note)
    return NotificationOut.model_validate(note)


@router.post("/read-all", status_code=status.HTTP_200_OK)
def mark_all_read(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    result = db.execute(
        update(Notification)
        .where(
            Notification.workspace_id == auth.workspace.id,
            or_(Notification.user_id == auth.user.id, Notification.user_id.is_(None)),
            Notification.is_read == False,  # noqa: E712
        )
        .values(is_read=True)
    )
    db.commit()
    return {"updated": result.rowcount}

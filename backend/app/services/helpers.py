import uuid

from sqlalchemy.orm import Session

from app.models import Activity, ActorType, Notification, NotificationType, User


def log_activity(
    db: Session,
    workspace_id: uuid.UUID,
    action: str,
    *,
    lead_id: uuid.UUID | None = None,
    actor_type: ActorType = ActorType.user,
    actor_id: uuid.UUID | None = None,
    description: str = "",
    metadata: dict | None = None,
) -> Activity:
    activity = Activity(
        workspace_id=workspace_id,
        lead_id=lead_id,
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        description=description,
        metadata_json=metadata or {},
    )
    db.add(activity)
    return activity


def notify_workspace(
    db: Session,
    workspace_id: uuid.UUID,
    type_: NotificationType,
    title: str,
    *,
    body: str = "",
    link: str | None = None,
    user_id: uuid.UUID | None = None,
) -> Notification:
    note = Notification(
        workspace_id=workspace_id,
        user_id=user_id,
        type=type_,
        title=title,
        body=body,
        link=link,
    )
    db.add(note)
    return note


def get_or_404(db: Session, model, obj_id: uuid.UUID, workspace_id: uuid.UUID):
    from fastapi import HTTPException, status

    obj = db.get(model, obj_id)
    if obj is None or getattr(obj, "workspace_id", None) != workspace_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Resource not found")
    return obj

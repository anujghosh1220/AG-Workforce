import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import Activity, ActorType, Lead
from app.schemas import ActivityOut

router = APIRouter(prefix="/activities", tags=["activities"])


@router.get("", response_model=Page[ActivityOut])
def list_activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    lead_id: uuid.UUID | None = None,
    action: str | None = None,
    actor_type: ActorType | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Activity).where(Activity.workspace_id == wid)
    if lead_id:
        query = query.where(Activity.lead_id == lead_id)
    if action:
        query = query.where(Activity.action == action)
    if actor_type:
        query = query.where(Activity.actor_type == actor_type)
    query = query.order_by(Activity.created_at.desc())

    items, total, page, page_size = paginate(db, query, page, page_size)
    out = [ActivityOut.model_validate(a) for a in items]
    return Page.build(out, total, page, page_size)

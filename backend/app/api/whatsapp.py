"""WhatsApp template management API — workspace-scoped, RBAC-enforced."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.core.database import get_db
from app.models import (
    Channel, Conversation, Lead, MessageTemplate, UserRole,
)
from app.schemas import ORMModel
from app.services.helpers import get_or_404
from app.services import whatsapp_templates as wt

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
ADMIN = (UserRole.owner, UserRole.admin)
MEMBER = (UserRole.owner, UserRole.admin, UserRole.member)


class TemplateSyncOut(BaseModel):
    synced: int
    created: int
    updated: int
    provider_count: int
    configured: bool


class TemplateSendIn(BaseModel):
    lead_id: uuid.UUID
    conversation_id: uuid.UUID | None = None
    components: list | None = Field(default=None, max_length=10)


class TemplateOut(ORMModel):
    id: uuid.UUID
    name: str
    channel: Channel
    language: str
    is_active: bool
    provider_template_id: str | None
    provider_status: str
    provider_category: str | None
    components: dict
    rejection_reason: str | None
    last_synced_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


@router.get("/templates")
def list_templates(
    status: str | None = Query(None),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    q = select(MessageTemplate).where(
        MessageTemplate.workspace_id == wid,
        MessageTemplate.channel == Channel.whatsapp)
    if status:
        q = q.where(MessageTemplate.provider_status == status)
    rows = db.scalars(q.order_by(MessageTemplate.name)).all()
    return [TemplateOut.model_validate(t) for t in rows]


@router.get("/templates/{template_id}")
def get_template(template_id: uuid.UUID,
                 auth: AuthContext = Depends(get_current_auth),
                 db: Session = Depends(get_db)):
    t = get_or_404(db, MessageTemplate, template_id, auth.workspace.id)
    if t.channel != Channel.whatsapp:
        raise HTTPException(404, "template not found")
    return TemplateOut.model_validate(t)


@router.post("/templates/sync", response_model=TemplateSyncOut)
def sync_templates(auth: AuthContext = Depends(require_roles(*ADMIN)),
                   db: Session = Depends(get_db)):
    try:
        res = wt.sync_templates(db, auth.workspace.id)
    except wt.TemplateError as exc:
        code = 503 if exc.code in ("not_configured", "provider_error",
                                   "whatsapp_rate_limited") else 502
        raise HTTPException(code, str(exc))
    db.commit()
    return {**res, "configured": True}


@router.post("/templates/{template_id}/send")
def send_template(template_id: uuid.UUID, body: TemplateSendIn,
                  auth: AuthContext = Depends(require_roles(*MEMBER)),
                  db: Session = Depends(get_db)):
    wid = auth.workspace.id
    t = get_or_404(db, MessageTemplate, template_id, wid)
    if t.channel != Channel.whatsapp:
        raise HTTPException(404, "template not found")
    lead = get_or_404(db, Lead, body.lead_id, wid)
    conv = None
    if body.conversation_id:
        conv = db.scalar(select(Conversation).where(
            Conversation.id == body.conversation_id,
            Conversation.workspace_id == wid))
        if conv is None:
            raise HTTPException(404, "conversation not found")
    to_phone = lead.phone
    if not to_phone:
        raise HTTPException(422, "lead has no WhatsApp phone number")
    try:
        res = wt.send_approved_template(
            db, wid, lead=lead, to_phone=to_phone, template=t,
            components=body.components, conv=conv,
            actor_id=auth.user.id)
    except wt.TemplateError as exc:
        raise HTTPException(
            409 if exc.code in ("template_not_approved", "not_synced")
            else 404 if exc.code == "not_found" else 422, str(exc))
    if not res.get("sent"):
        raise HTTPException(
            409 if res.get("status") == "blocked" else 502,
            res.get("reason", "send failed"))
    db.commit()
    return res

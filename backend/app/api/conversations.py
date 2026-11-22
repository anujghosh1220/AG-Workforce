import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    ActorType, Channel, Conversation, FollowUp, FollowUpStatus, Lead,
    Message, MessageDirection, MessageStatus, UserRole,
)
from app.schemas import (
    ConversationDetailOut, FollowUpCreate, FollowUpOut, InboundMessageIn,
    ReplyDraftOut, SendReplyIn,
)
import app.services.outreach as outreach
from app.services.conversation_engine import (
    add_message, handle_inbound, recent_history, schedule_follow_up_manual,
)
from app.services.helpers import get_or_404, log_activity

router = APIRouter(prefix="/conversations", tags=["conversations"])
WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)


def _conv_out(c: Conversation) -> ConversationDetailOut:
    data = ConversationDetailOut.model_validate(c).model_dump()
    data["lead_business_name"] = c.lead.business_name if c.lead else None
    msgs = []
    for m in sorted(c.messages, key=lambda x: x.created_at):
        msgs.append({
            "id": str(m.id), "conversation_id": str(m.conversation_id),
            "direction": m.direction.value, "channel": m.channel.value,
            "subject": m.subject, "body": m.body, "status": m.status.value,
            "is_ai_draft": m.is_ai_draft, "approval_id": str(m.approval_id) if m.approval_id else None,
            "sent_at": m.sent_at.isoformat() if m.sent_at else None,
            "created_at": m.created_at.isoformat() if m.created_at else None,
        })
    data["messages"] = msgs
    return ConversationDetailOut(**{**data, "messages": msgs})


@router.get("", response_model=Page[ConversationDetailOut])
def list_conversations(
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    channel: Channel | None = None,
    state: str | None = None,
    needs_human: bool | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = (
        select(Conversation)
        .where(Conversation.workspace_id == wid)
        .options(selectinload(Conversation.messages), selectinload(Conversation.lead))
        .order_by(Conversation.updated_at.desc())
    )
    if channel:
        query = query.where(Conversation.channel == channel)
    if state:
        query = query.where(Conversation.state == state)
    if needs_human is True:
        query = query.where(Conversation.needs_human.is_(True))
    elif needs_human is False:
        query = query.where(Conversation.needs_human.is_(False))
    if q:
        query = query.join(Lead, Lead.id == Conversation.lead_id).where(
            or_(Lead.business_name.ilike(f"%{q.strip()}%"),
                Lead.email.ilike(f"%{q.strip()}%"),
                Lead.city.ilike(f"%{q.strip()}%"))
        )
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([_conv_out(c) for c in items], total, page, page_size)


@router.get("/{conv_id}", response_model=ConversationDetailOut)
def get_conversation(conv_id: uuid.UUID,
                     auth: AuthContext = Depends(get_current_auth),
                     db: Session = Depends(get_db)):
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    db.refresh(conv, ["messages", "lead"])
    return _conv_out(conv)


@router.post("/{conv_id}/messages/inbound", response_model=dict)
def simulate_inbound(conv_id: uuid.UUID, body: InboundMessageIn,
                     auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                     db: Session = Depends(get_db)):
    """Dev/testing endpoint — records an inbound message as if received.
    Production inbound arrives via /webhooks/email/resend."""
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    lead = conv.lead
    value = lead.email if conv.channel == Channel.email else lead.phone
    result = handle_inbound(
        db, auth.workspace.id, channel=conv.channel,
        from_address=body.from_address or value or "unknown",
        to_address=body.to_address, subject=body.subject, body=body.body,
        external_id=body.external_id, thread_id=body.thread_id or str(conv.id),
    )
    return result


@router.post("/{conv_id}/suggest", response_model=ReplyDraftOut)
def suggest_reply(conv_id: uuid.UUID,
                  auth: AuthContext = Depends(get_current_auth),
                  db: Session = Depends(get_db)):
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    db.refresh(conv, ["messages", "lead"])
    return outreach.generate_reply_draft(db, auth.workspace.id, conv)


@router.post("/{conv_id}/draft-reply", response_model=dict)
def create_reply_draft(conv_id: uuid.UUID, body: SendReplyIn,
                       auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                       db: Session = Depends(get_db)):
    """Create an AI outbound draft + approval for human review before send."""
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    msg = outreach.create_outbound_draft(
        db, auth.workspace.id, conv,
        body.subject or f"Re: {conv.lead.business_name}", body.body,
        agent_slug="communication-manager")
    return {"message_id": str(msg.id), "approval_id": str(msg.approval_id) if msg.approval_id else None,
            "status": msg.status.value}


@router.post("/{conv_id}/send", response_model=dict)
def send_reply(conv_id: uuid.UUID, body: SendReplyIn,
               auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
               db: Session = Depends(get_db)):
    """Human-initiated send — goes through compliance checks immediately."""
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    msg = add_message(db, auth.workspace.id, conv, direction=MessageDirection.outbound,
                      body=body.body, channel=conv.channel,
                      subject=body.subject or f"Re: {conv.lead.business_name}",
                      status=MessageStatus.draft, is_ai_draft=False)
    db.commit()
    return outreach.send_message(db, auth.workspace.id, msg)


@router.post("/{conv_id}/handoff", response_model=ConversationDetailOut)
def handoff(conv_id: uuid.UUID, auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
            db: Session = Depends(get_db)):
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    conv.needs_human = True
    conv.state = "human_handoff"
    log_activity(db, auth.workspace.id, "conversation.handoff", lead_id=conv.lead_id,
                 actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Conversation handed to human: {conv.lead.business_name}")
    db.commit()
    db.refresh(conv, ["messages", "lead"])
    return _conv_out(conv)


@router.post("/{conv_id}/resume-ai", response_model=ConversationDetailOut)
def resume_ai(conv_id: uuid.UUID, auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
              db: Session = Depends(get_db)):
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    conv.needs_human = False
    if conv.state == "human_handoff":
        conv.state = "engaged"
    db.commit()
    db.refresh(conv, ["messages", "lead"])
    return _conv_out(conv)


@router.get("/{conv_id}/follow-ups", response_model=list[FollowUpOut])
def list_follow_ups(conv_id: uuid.UUID,
                    auth: AuthContext = Depends(get_current_auth),
                    db: Session = Depends(get_db)):
    get_or_404(db, Conversation, conv_id, auth.workspace.id)
    rows = db.scalars(select(FollowUp).where(FollowUp.conversation_id == conv_id)
                      .order_by(FollowUp.scheduled_at)).all()
    return [FollowUpOut.model_validate(f) for f in rows]


@router.post("/{conv_id}/follow-ups", response_model=FollowUpOut,
             status_code=status.HTTP_201_CREATED)
def create_follow_up(conv_id: uuid.UUID, body: FollowUpCreate,
                     auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                     db: Session = Depends(get_db)):
    conv = get_or_404(db, Conversation, conv_id, auth.workspace.id)
    fu = schedule_follow_up_manual(db, auth.workspace.id, conv, body.hours, body.max_attempts)
    return FollowUpOut.model_validate(fu)


@router.delete("/follow-ups/{fu_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_follow_up(fu_id: uuid.UUID,
                     auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                     db: Session = Depends(get_db)):
    fu = db.scalar(select(FollowUp).where(FollowUp.id == fu_id,
                                          FollowUp.workspace_id == auth.workspace.id))
    if fu is None:
        raise HTTPException(404, "Follow-up not found")
    fu.status = FollowUpStatus.cancelled
    db.commit()

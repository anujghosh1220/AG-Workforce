"""Sales campaigns — owner-authorized batch outreach."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    ActorType, Campaign, CampaignStatus, Channel, Lead, LeadStage,
    Message, MessageDirection, MessageStatus, UserRole,
)
from app.schemas import CampaignAuthorize, CampaignCreate, CampaignOut, EligibilityOut
import app.services.outreach as outreach
from app.services.helpers import get_or_404, log_activity
from app.services.permissions import spec_for

router = APIRouter(prefix="/campaigns", tags=["campaigns"])
WRITE_ROLES = (UserRole.owner, UserRole.admin)


def _out(c: Campaign) -> CampaignOut:
    return CampaignOut.model_validate(c)


@router.get("", response_model=Page[CampaignOut])
def list_campaigns(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    q = select(Campaign).where(Campaign.workspace_id == auth.workspace.id).order_by(Campaign.created_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([_out(c) for c in items], total, page, page_size)


@router.post("", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
def create_campaign(body: CampaignCreate,
                    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                    db: Session = Depends(get_db)):
    wid = auth.workspace.id
    # validate leads belong to workspace
    leads = db.scalars(select(Lead).where(Lead.workspace_id == wid, Lead.id.in_(body.lead_ids))).all()
    if not leads:
        raise HTTPException(400, "No valid leads in this workspace")
    c = Campaign(workspace_id=wid, name=body.name, channel=body.channel,
                 lead_ids=[str(l.id) for l in leads], instructions=body.instructions,
                 stats={"total": len(leads), "eligible": 0, "blocked": 0,
                        "drafted": 0, "sent": 0, "failed": 0})
    db.add(c)
    db.flush()
    log_activity(db, wid, "campaign.created", actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Campaign '{c.name}' created with {len(leads)} leads")
    db.commit()
    db.refresh(c)
    return _out(c)


@router.get("/{campaign_id}/eligibility", response_model=list[EligibilityOut])
def campaign_eligibility(campaign_id: uuid.UUID,
                         auth: AuthContext = Depends(get_current_auth),
                         db: Session = Depends(get_db)):
    c = get_or_404(db, Campaign, campaign_id, auth.workspace.id)
    out = []
    for lid in c.lead_ids:
        lead = db.get(Lead, uuid.UUID(lid))
        if lead is None:
            continue
        e = outreach.eligibility(db, auth.workspace.id, lead, c.channel)
        out.append(EligibilityOut(lead_id=lead.id, business_name=lead.business_name, **e))
    return out


@router.post("/{campaign_id}/authorize", response_model=CampaignOut)
def authorize_campaign(campaign_id: uuid.UUID,
                       body: CampaignAuthorize,
                       auth: AuthContext = Depends(require_roles(UserRole.owner)),
                       db: Session = Depends(get_db)):
    """Owner-only campaign authorization — required before any send."""
    if not body.confirm:
        raise HTTPException(400, "Authorization not confirmed")
    c = get_or_404(db, Campaign, campaign_id, auth.workspace.id)
    if c.status != CampaignStatus.draft:
        raise HTTPException(409, f"Campaign is {c.status.value}, cannot authorize")
    c.status = CampaignStatus.authorized
    c.authorized_by_user_id = auth.user.id
    c.authorized_at = datetime.now(timezone.utc)
    log_activity(db, auth.workspace.id, "campaign.authorized", actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Campaign '{c.name}' authorized")
    db.commit()
    db.refresh(c)
    return _out(c)


@router.post("/{campaign_id}/generate", response_model=CampaignOut)
def generate_drafts(campaign_id: uuid.UUID,
                    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                    db: Session = Depends(get_db)):
    """Generate personalized drafts for eligible leads (no sending)."""
    c = get_or_404(db, Campaign, campaign_id, auth.workspace.id)
    if c.status not in (CampaignStatus.draft, CampaignStatus.authorized):
        raise HTTPException(409, "Campaign cannot generate drafts in current state")
    wid = auth.workspace.id
    stats = dict(c.stats or {})
    stats.update({"eligible": 0, "blocked": 0, "drafted": 0, "sent": 0, "failed": 0})
    for lid in c.lead_ids:
        lead = db.get(Lead, uuid.UUID(lid))
        if lead is None:
            continue
        e = outreach.eligibility(db, wid, lead, c.channel)
        if not e["allowed"]:
            stats["blocked"] += 1
            continue
        stats["eligible"] += 1
        conv = None
        if c.channel == Channel.email:
            conv = _conv_for(db, wid, lead.id)
        draft = outreach.generate_intro_draft(db, wid, lead, c.instructions)
        if conv is None:
            conv = _conv_for(db, wid, lead.id)
        outreach.create_outbound_draft(db, wid, conv, draft["subject"], draft["body"],
                                       agent_slug="sales-executive")
        stats["drafted"] += 1
    c.stats = stats
    db.commit()
    db.refresh(c)
    return _out(c)


def _conv_for(db: Session, wid, lead_id):
    from app.services.conversation_engine import get_or_create_conversation
    return get_or_create_conversation(db, wid, lead_id, Channel.email)


@router.post("/{campaign_id}/send", response_model=CampaignOut)
def send_campaign(campaign_id: uuid.UUID,
                  auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                  db: Session = Depends(get_db)):
    """Send all pending-approval drafts — each send re-checks compliance.
    Only works on authorized campaigns."""
    c = get_or_404(db, Campaign, campaign_id, auth.workspace.id)
    if c.status != CampaignStatus.authorized:
        raise HTTPException(409, "Campaign must be authorized by an owner first")
    c.status = CampaignStatus.sending
    db.commit()

    stats = dict(c.stats or {})
    stats["sent"] = 0
    stats["failed"] = 0
    for lid in c.lead_ids:
        lead = db.get(Lead, uuid.UUID(lid))
        if lead is None:
            continue
        conv = _conv_for(db, auth.workspace.id, lead.id)
        msgs = db.scalars(select(Message).where(
            Message.conversation_id == conv.id,
            Message.direction == MessageDirection.outbound,
            Message.status == MessageStatus.pending_approval,
        )).all()
        for m in msgs:
            res = outreach.send_message(db, auth.workspace.id, m)
            if res.get("sent"):
                stats["sent"] += 1
            else:
                stats["failed"] += 1
    c.stats = stats
    c.status = CampaignStatus.completed
    db.commit()
    db.refresh(c)
    return _out(c)

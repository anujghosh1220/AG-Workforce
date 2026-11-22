"""Unified conversation engine — channel-agnostic inbound handling."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.llm.base import get_llm
from app.integrations.llm.heuristic import analyze as heuristic_analyze
from app.models import (
    ActorType, Agent, AssigneeType, Channel, ChannelConsent, ConsentStatus,
    Conversation, FollowUp, FollowUpStatus, Lead, LeadStage, Message,
    MessageDirection, MessageStatus, NotificationType, SuppressionRecord,
    Task, TaskPriority, TaskStatus,
)
from app.services.compliance import record_consent
from app.services.helpers import log_activity, notify_workspace


def _now():
    return datetime.now(timezone.utc)


def find_lead_by_contact(db: Session, wid: uuid.UUID, channel: Channel, address: str):
    addr = (address or "").strip().lower()
    if channel == Channel.email:
        m = re.search(r"<([^>]+)>", addr)
        if m:
            addr = m.group(1).strip()
        return db.scalar(select(Lead).where(Lead.workspace_id == wid, Lead.email == addr).limit(1))
    digits = re.sub(r"\D", "", addr)
    leads = db.scalars(select(Lead).where(Lead.workspace_id == wid, Lead.phone.is_not(None))).all()
    for l in leads:
        if re.sub(r"\D", "", l.phone or "")[-10:] == digits[-10:]:
            return l
    return None


def get_or_create_conversation(db: Session, wid: uuid.UUID, lead_id: uuid.UUID,
                               channel: Channel, thread_id: str | None = None) -> Conversation:
    q = select(Conversation).where(
        Conversation.workspace_id == wid,
        Conversation.lead_id == lead_id,
        Conversation.channel == channel,
    )
    if thread_id:
        q = q.where(Conversation.external_thread_id == thread_id)
    conv = db.scalar(q.order_by(Conversation.updated_at.desc()).limit(1))
    if conv is None:
        conv = Conversation(workspace_id=wid, lead_id=lead_id, channel=channel,
                            external_thread_id=thread_id, state="new")
        db.add(conv)
        db.flush()
    elif thread_id and not conv.external_thread_id:
        conv.external_thread_id = thread_id
    return conv


def add_message(db: Session, wid: uuid.UUID, conv: Conversation, *,
                direction: MessageDirection, body: str, channel: Channel,
                subject: str | None = None, status: MessageStatus = MessageStatus.draft,
                is_ai_draft: bool = False, approval_id=None) -> Message:
    msg = Message(workspace_id=wid, conversation_id=conv.id, direction=direction,
                  channel=channel, subject=subject, body=body, status=status,
                  is_ai_draft=is_ai_draft, approval_id=approval_id)
    db.add(msg)
    db.flush()
    conv.updated_at = _now()
    return msg


def recent_history(db: Session, conv_id: uuid.UUID, limit: int = 20) -> list[Message]:
    return list(db.scalars(
        select(Message).where(Message.conversation_id == conv_id)
        .order_by(Message.created_at.desc()).limit(limit)
    ).all())[::-1]


def knowledge_context(db: Session, wid: uuid.UUID, limit: int = 10) -> str:
    from app.models import ProductKnowledge
    rows = db.scalars(
        select(ProductKnowledge).where(ProductKnowledge.workspace_id == wid)
        .order_by(ProductKnowledge.sort_order).limit(limit)
    ).all()
    return "\n".join(f"- {r.title}: {r.content[:400]}" for r in rows if r.content)


def analyze_inbound(db: Session, wid: uuid.UUID, conv: Conversation,
                    body: str, lead: Lead | None) -> dict:
    llm = get_llm()
    history = "\n".join(
        f"{'Prospect' if m.direction == MessageDirection.inbound else 'Us'}: {m.body[:300]}"
        for m in recent_history(db, conv.id, 10)
    )
    if llm is not None:
        try:
            from pydantic import BaseModel, Field
            class Analysis(BaseModel):
                intent: str = Field(description="greeting|question|objection|buying_signal|opt_out|human_request|other")
                conversation_state: str
                summary: str
                next_action: str
                needs_human: bool = False
                confidence: str = "medium"
            user = (
                f"Conversation so far:\n{history or '(new)'}\n\n"
                f"New prospect message:\n{body}\n\n"
                "Classify the message using only the allowed intents."
            )
            out = llm.generate_json(
                "You are a sales conversation analyst for a cafe management software company. "
                "Classify prospect messages accurately. Never invent facts.",
                user, Analysis)
            d = out.model_dump()
            d["provider"] = llm.name
            return d
        except Exception:
            pass
    return heuristic_analyze(body)


def handle_inbound(db: Session, wid: uuid.UUID, *, channel: Channel,
                   from_address: str, to_address: str | None,
                   subject: str | None, body: str,
                   external_id: str | None = None,
                   thread_id: str | None = None) -> dict:
    lead = find_lead_by_contact(db, wid, channel, from_address)
    if lead is None:
        return {"matched": False, "reason": "no lead for sender"}

    conv = get_or_create_conversation(db, wid, lead.id, channel, thread_id or external_id)
    msg = add_message(db, wid, conv, direction=MessageDirection.inbound,
                      body=body, channel=channel, subject=subject,
                      status=MessageStatus.delivered)

    analysis = analyze_inbound(db, wid, conv, body, lead)
    intent = analysis.get("intent", "other")
    conv.last_intent = intent
    conv.state = analysis.get("conversation_state") or conv.state
    conv.summary = (analysis.get("summary") or "")[:2000]
    conv.needs_human = bool(analysis.get("needs_human"))

    if intent == "opt_out":
        record_consent(db, wid, channel, from_address, ConsentStatus.withdrawn,
                       source="inbound_reply", lead_id=lead.id)
        exists = db.scalar(select(SuppressionRecord.id).where(
            SuppressionRecord.workspace_id == wid,
            SuppressionRecord.channel == channel,
            SuppressionRecord.value == from_address.strip().lower()))
        if not exists:
            db.add(SuppressionRecord(workspace_id=wid, channel=channel,
                                     value=from_address.strip().lower(),
                                     reason="prospect opted out"))
        conv.state = "opted_out"
        conv.status = "closed"
        lead.stage = LeadStage.do_not_contact
        for fu in db.scalars(select(FollowUp).where(
                FollowUp.conversation_id == conv.id,
                FollowUp.status == FollowUpStatus.scheduled)).all():
            fu.status = FollowUpStatus.cancelled
        notify_workspace(db, wid, NotificationType.new_reply,
                         f"Opt-out received from {lead.business_name}",
                         body=body[:200], link="/conversations")
        db.commit()
        return {"matched": True, "conversation_id": str(conv.id), "intent": intent,
                "state": conv.state, "opted_out": True}

    if intent == "buying_signal":
        if lead.stage in (LeadStage.contacted, LeadStage.replied, LeadStage.researched, LeadStage.qualified):
            lead.stage = LeadStage.interested
        notify_workspace(db, wid, NotificationType.interested_prospect,
                         f"Interested prospect: {lead.business_name}",
                         body=body[:200], link="/conversations")
    elif intent == "human_request":
        conv.state = "human_handoff"
        conv.needs_human = True
        notify_workspace(db, wid, NotificationType.agent_error,
                         f"Human assistance requested by {lead.business_name}",
                         body=body[:200], link="/conversations")
    else:
        if lead.stage == LeadStage.contacted:
            lead.stage = LeadStage.replied
        notify_workspace(db, wid, NotificationType.new_reply,
                         f"New reply from {lead.business_name}",
                         body=body[:200], link="/conversations")

    lead.last_contacted_at = _now()
    for fu in db.scalars(select(FollowUp).where(
            FollowUp.conversation_id == conv.id,
            FollowUp.status == FollowUpStatus.scheduled)).all():
        fu.status = FollowUpStatus.cancelled

    # ---- Executive Assistant: demo/appointment scheduling turns
    sched = None
    try:
        from app.services import scheduling_intent
        sched = scheduling_intent.handle_scheduling_message(db, wid, conv, lead, body)
    except Exception:
        import logging
        logging.getLogger("agworkforce.conversations").exception(
            "scheduling intent handler failed")
        sched = None
    if sched and sched.get("reply"):
        sched = _deliver_scheduling_reply(db, wid, conv, lead, sched)
        if sched.get("kind") in ("proposed", "clarify", "reschedule_offer",
                                 "conflict", "pending_approval", "no_slots"):
            if conv.state not in ("opted_out", "closed", "human_handoff",
                                  "demo_scheduled"):
                conv.state = "scheduling"

    if conv.state not in ("opted_out", "closed", "human_handoff", "demo_scheduled",
                          "scheduling"):
        _schedule_follow_up(db, wid, conv, lead, channel)

    if conv.needs_human:
        agent = db.scalar(select(Agent).where(Agent.workspace_id == wid,
                                              Agent.slug == "executive-assistant"))
        db.add(Task(workspace_id=wid,
                    title=f"Review conversation with {lead.business_name}",
                    description=f"Intent: {intent}. Latest message: {body[:500]}",
                    assignee_type=AssigneeType.human if agent is None else AssigneeType.agent,
                    assignee_agent_id=agent.id if agent else None,
                    priority=TaskPriority.high if intent in ("human_request", "buying_signal") else TaskPriority.medium,
                    status=TaskStatus.pending, lead_id=lead.id))

    log_activity(db, wid, "conversation.inbound", lead_id=lead.id,
                 actor_type=ActorType.system, actor_id=None,
                 description=f"Inbound {channel.value} from {lead.business_name} (intent: {intent})",
                 metadata={"conversation_id": str(conv.id), "message_id": str(msg.id),
                           "intent": intent, "provider": analysis.get("provider"),
                           "scheduling": sched})
    db.commit()
    out = {"matched": True, "conversation_id": str(conv.id), "lead_id": str(lead.id),
           "intent": intent, "state": conv.state, "message_id": str(msg.id)}
    if sched:
        out["scheduling"] = {k: v for k, v in sched.items() if k != "reply"}
        out["scheduling"]["reply_sent"] = bool(sched.get("reply_sent"))
    return out


def _deliver_scheduling_reply(db: Session, wid: uuid.UUID, conv: Conversation,
                              lead: Lead, sched: dict) -> dict:
    """Persist the EA's reply as an outbound draft through the normal
    approval/channel pipeline; auto-send only when the Communication Manager
    is explicitly permitted for that channel."""
    import app.services.outreach as outreach

    reply = sched["reply"]
    subject = f"Re: {lead.business_name} — scheduling"
    msg = outreach.create_outbound_draft(
        db, wid, conv, subject, reply, agent_slug="communication-manager")

    agent = db.scalar(select(Agent).where(
        Agent.workspace_id == wid, Agent.slug == "communication-manager"))
    autonomous_actions = set((agent.permissions or {}).get("autonomous_actions", [])) if agent else set()
    required = "send_email" if conv.channel == Channel.email else "send_whatsapp_business"
    sent = False
    if agent and agent.enabled and required in autonomous_actions:
        res = outreach.send_message(db, wid, msg)
        sent = bool(res.get("sent"))
    sched["reply_sent"] = sent
    sched["message_id"] = str(msg.id)
    sched["approval_id"] = str(msg.approval_id) if msg.approval_id else None
    return sched


def _schedule_follow_up(db: Session, wid: uuid.UUID, conv: Conversation,
                        lead: Lead, channel: Channel, hours: int = 48,
                        max_attempts: int = 3) -> FollowUp | None:
    existing = db.scalar(select(FollowUp).where(
        FollowUp.conversation_id == conv.id,
        FollowUp.status == FollowUpStatus.scheduled).limit(1))
    if existing:
        return existing
    fu = FollowUp(workspace_id=wid, conversation_id=conv.id, lead_id=lead.id,
                  channel=channel, scheduled_at=_now() + timedelta(hours=hours),
                  interval_hours=hours, max_attempts=max_attempts,
                  status=FollowUpStatus.scheduled)
    db.add(fu)
    db.flush()
    return fu


def schedule_follow_up_manual(db: Session, wid: uuid.UUID, conv: Conversation,
                              hours: int = 24, max_attempts: int = 3) -> FollowUp:
    fu = _schedule_follow_up(db, wid, conv, conv.lead, conv.channel, hours, max_attempts)
    db.commit()
    return fu


def cancel_follow_up(db: Session, fu: FollowUp) -> None:
    fu.status = FollowUpStatus.cancelled
    db.commit()

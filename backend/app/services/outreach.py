"""Sales outreach: eligibility, draft generation, approved sending."""

from __future__ import annotations



import uuid

from datetime import datetime, timezone



from sqlalchemy import select

from sqlalchemy.orm import Session



from app.integrations.email.base import OutboundEmail, get_email_provider

from app.integrations.whatsapp.base import get_whatsapp_provider

from app.integrations.llm.base import get_llm

from app.integrations.llm.heuristic import draft_reply as heuristic_draft

from app.models import (

    ActorType, Agent, Approval, ApprovalActionType, ApprovalStatus, Channel,

    Conversation, Lead, LeadStage, Message, MessageDirection, MessageStatus,

    RiskLevel,

)

from app.services import compliance

from app.services.conversation_engine import (

    add_message, knowledge_context, recent_history,

)

from app.services.helpers import log_activity





def _now():

    return datetime.now(timezone.utc)





def eligibility(db: Session, wid: uuid.UUID, lead: Lead, channel: Channel) -> dict:

    value = lead.email if channel == Channel.email else lead.phone

    reason = compliance.outbound_block_reason(db, wid, channel, value=value, lead_id=lead.id)

    status = compliance.consent_status(db, wid, channel, value) if value else None

    return {

        "allowed": reason is None,

        "reason": reason,

        "has_contact": bool(value),

        "contact_value": value,

        "consent_status": status.value if status else None,

        "channel": channel.value,

    }





def generate_intro_draft(db: Session, wid: uuid.UUID, lead: Lead,

                         instructions: str = "") -> dict:

    knowledge = knowledge_context(db, wid)

    facts = {

        "business_name": lead.business_name, "business_type": lead.business_type,

        "city": lead.city, "state": lead.state, "country": lead.country,

        "website": lead.website, "contact_person": lead.contact_person,

        "lead_score": lead.lead_score,

    }

    llm = get_llm()

    if llm is not None:

        try:

            from pydantic import BaseModel, Field

            class Draft(BaseModel):

                subject: str

                body: str

                grounded_in: list[str] = Field(default_factory=list)

            user = (

                "Write a short professional first-contact email introducing our cafe "

                "management & ordering software.\n\n"

                f"Verified prospect facts (use ONLY these):\n{facts}\n\n"

                f"Approved product knowledge:\n{knowledge or '(none configured)'}\n\n"

                f"Instructions: {instructions or 'none'}\n\n"

                "Rules: no discounts/pricing promises, no invented claims, under 150 "

                "words, clear low-pressure CTA. grounded_in = fact keys used."

            )

            out = llm.generate_json(

                "You are the Sales Executive for a B2B cafe software company. "

                "Write honest personalized outreach grounded only in provided facts.",

                user, Draft)

            return {"subject": out.subject, "body": out.body,

                    "grounded_in": out.grounded_in, "provider": llm.name}

        except Exception:

            pass

    d = heuristic_draft({"business_name": lead.business_name, "city": lead.city})

    return {"subject": d["subject"], "body": d["body"],

            "grounded_in": ["business_name", "city"], "provider": "heuristic"}





def generate_reply_draft(db: Session, wid: uuid.UUID, conv: Conversation) -> dict:

    lead = conv.lead

    history = "\n".join(

        f"{'Prospect' if m.direction == MessageDirection.inbound else 'Us'}: {m.body[:400]}"

        for m in recent_history(db, conv.id, 15)

    )

    knowledge = knowledge_context(db, wid)

    llm = get_llm()

    if llm is not None:

        try:

            from pydantic import BaseModel, Field

            class Reply(BaseModel):

                subject: str

                body: str

                needs_human_review: bool = False

                reason: str = ""

            user = (

                f"Prospect: {lead.business_name} ({lead.city or 'unknown city'})\n\n"

                f"Conversation history:\n{history}\n\n"

                f"Approved product knowledge:\n{knowledge or '(none)'}\n\n"

                "Draft the next reply. Answer only from approved knowledge; if the "

                "prospect asks something unsupported set needs_human_review=true and "

                "explain; never invent prices/features; under 150 words."

            )

            out = llm.generate_json(

                "You are the Communication Manager for a cafe software sales team. "

                "Reply helpfully, honestly, only from approved knowledge.",

                user, Reply)

            return {"subject": out.subject, "body": out.body,

                    "needs_human_review": out.needs_human_review,

                    "reason": out.reason, "provider": llm.name}

        except Exception:

            pass

    d = heuristic_draft({"business_name": lead.business_name, "city": lead.city})

    return {"subject": d["subject"], "body": d["body"],

            "needs_human_review": False, "reason": "", "provider": "heuristic"}





def create_outbound_draft(db: Session, wid: uuid.UUID, conv: Conversation,

                          subject: str, body: str, *, agent_slug: str = "sales-executive",

                          approval_required: bool = True) -> Message:

    agent = db.scalar(select(Agent).where(Agent.workspace_id == wid, Agent.slug == agent_slug))

    msg = add_message(db, wid, conv, direction=MessageDirection.outbound,

                      body=body, channel=conv.channel, subject=subject,

                      status=MessageStatus.pending_approval if approval_required else MessageStatus.draft,

                      is_ai_draft=True)

    if approval_required:

        approval = Approval(

            workspace_id=wid,

            action_type=ApprovalActionType.send_email if conv.channel == Channel.email else ApprovalActionType.send_whatsapp,

            risk_level=RiskLevel.medium,

            title=f"Send {conv.channel.value} to {conv.lead.business_name}",

            payload={"message_id": str(msg.id), "conversation_id": str(conv.id),

                     "subject": subject, "body": body[:2000]},

            lead_id=conv.lead_id,

            requested_by_agent_id=agent.id if agent else None,

            status=ApprovalStatus.pending,

        )

        db.add(approval)

        db.flush()

        msg.approval_id = approval.id

    log_activity(db, wid, "message.draft_created", lead_id=conv.lead_id,

                 actor_type=ActorType.agent, actor_id=agent.id if agent else None,

                 description=f"Draft {conv.channel.value} reply for {conv.lead.business_name}",

                 metadata={"message_id": str(msg.id), "conversation_id": str(conv.id)})

    db.commit()

    return msg





def send_message(db: Session, wid: uuid.UUID, msg: Message) -> dict:

    """Send an approved outbound message. Re-checks compliance immediately."""

    conv = db.get(Conversation, msg.conversation_id)

    lead = conv.lead if conv else None

    channel = msg.channel

    value = lead.email if channel == Channel.email else lead.phone



    reason = compliance.outbound_block_reason(db, wid, channel, value=value,

                                              lead_id=lead.id if lead else None)

    if reason:

        msg.status = MessageStatus.failed

        db.commit()

        return {"sent": False, "reason": reason}



    if channel == Channel.whatsapp:

        # 24-hour customer-service window: free-form only inside window;

        # outside requires an approved template (not yet supported -> block).

        last_in = None

        if conv:

            from app.models import Message as _M

            last_in = db.scalar(select(_M.created_at).where(

                _M.conversation_id == conv.id,

                _M.direction == MessageDirection.inbound,

            ).order_by(_M.created_at.desc()).limit(1))

        from datetime import timedelta

        within = last_in is not None and (_now() - last_in) < timedelta(hours=24)

        provider = get_whatsapp_provider()

        if provider is None:

            msg.status = MessageStatus.failed

            db.commit()

            return {"sent": False, "reason": "whatsapp provider not configured"}

        if not within:

            msg.status = MessageStatus.failed

            db.commit()

            return {"sent": False, "reason": "outside 24h customer-service window - approved template required"}

        try:

            from app.integrations.whatsapp.base import WhatsAppMessage

            res = provider.send_text(WhatsAppMessage(to=value or "", body=msg.body))
            msg.provider_message_id = res.provider_message_id or None

        except Exception as exc:

            msg.status = MessageStatus.failed

            db.commit()

            return {"sent": False, "reason": str(exc)[:300]}

        msg.status = MessageStatus.sent

        msg.sent_at = _now()

        if conv and conv.state == "new":

            conv.state = "contacted"

        if lead and lead.stage in (LeadStage.researched, LeadStage.qualified, LeadStage.discovered):

            lead.stage = LeadStage.contacted

        if lead:

            lead.last_contacted_at = _now()

        db.commit()

        return {"sent": True, "message_id": str(msg.id)}

    if channel != Channel.email:

        msg.status = MessageStatus.failed

        db.commit()

        return {"sent": False, "reason": "unsupported channel"}



    provider = get_email_provider()

    if provider is None:

        msg.status = MessageStatus.failed

        db.commit()

        return {"sent": False, "reason": "email provider not configured"}

    try:

        sent = provider.send(OutboundEmail(

            to=value, subject=msg.subject or "", body=msg.body,

            headers={"X-AGW-Message-Id": str(msg.id)},

        ))
        msg.provider_message_id = sent.provider_message_id or None

    except Exception as exc:

        msg.status = MessageStatus.failed

        db.commit()

        return {"sent": False, "reason": str(exc)[:300]}



    msg.status = MessageStatus.sent

    msg.sent_at = _now()

    if conv:

        if conv.state == "new":

            conv.state = "contacted"

        if lead and lead.stage in (LeadStage.researched, LeadStage.qualified, LeadStage.discovered):

            lead.stage = LeadStage.contacted

        if lead:

            lead.last_contacted_at = _now()

    log_activity(db, wid, "message.sent", lead_id=lead.id if lead else None,

                 actor_type=ActorType.system, actor_id=None,

                 description=f"Email sent to {lead.business_name if lead else value}",

                 metadata={"message_id": str(msg.id), "provider": provider.name,

                           "provider_message_id": sent.provider_message_id})

    db.commit()

    return {"sent": True, "message_id": str(msg.id)}


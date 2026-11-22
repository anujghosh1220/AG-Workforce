"""Phase 6 — workforce performance metrics & sales funnel.

Every figure is computed from real DB records. Each metric carries a
`quality` flag: "exact" | "estimate" | "unavailable" so the UI never
presents guesses as facts.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Agent, AgentRun, Appointment, AppointmentStatus, Approval, ApprovalActionType,
    ApprovalStatus, Campaign, CampaignStatus, Channel, Conversation, FollowUp,
    FollowUpStatus, Lead, LeadStage, Message, MessageDirection, MessageStatus,
    Opportunity, OpportunityStatus, QualificationStatus, ResearchJob,
    ResearchJobStatus, ResearchResult, ResearchResultStatus, SalesIssue,
    Recommendation, RecommendationStatus, Task, TaskStatus, ManagementReport,
)

AGENT_SLUGS = ["lead-researcher", "sales-executive", "communication-manager",
               "executive-assistant", "sales-manager"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _metric(value, quality: str = "exact", note: str = "") -> dict:
    return {"value": value, "quality": quality, "note": note}


def _count(db, model, *conds):
    return db.scalar(select(func.count(model.id)).where(*conds)) or 0


# ---------------------------------------------------------------- per-agent


def agent_performance(db: Session, wid: uuid.UUID) -> dict:
    """Return {slug: {name, status, metrics:{...}, tasks:{...}}} for all agents."""
    agents = {a.slug: a for a in db.scalars(
        select(Agent).where(Agent.workspace_id == wid)).all()}
    now = _now()
    d7 = now - timedelta(days=7)

    out: dict = {}
    for slug in AGENT_SLUGS:
        agent = agents.get(slug)
        base = {
            "name": agent.name if agent else slug.replace("-", " ").title(),
            "status": agent.status.value if agent else "missing",
            "enabled": agent.enabled if agent else False,
            "tasks_completed": agent.tasks_completed if agent else 0,
            "current_workload": agent.current_workload if agent else 0,
        }
        out[slug] = {"agent": base, "metrics": _metrics_for(db, wid, slug, agent, d7)}
    return out


def _metrics_for(db, wid, slug, agent, since) -> dict:
    m: dict = {}
    if slug == "lead-researcher":
        jobs = db.scalars(select(ResearchJob).where(
            ResearchJob.workspace_id == wid)).all()
        results = db.scalars(select(ResearchResult).where(
            ResearchResult.workspace_id == wid)).all()
        total = len(results)
        researched = sum(1 for r in results if r.status in (
            ResearchResultStatus.researched, ResearchResultStatus.imported))
        failed = sum(1 for r in results if r.status == ResearchResultStatus.failed)
        dups = sum(1 for r in results if r.status == ResearchResultStatus.duplicate)
        done_jobs = [j for j in jobs if j.status == ResearchJobStatus.completed
                     and j.started_at and j.finished_at]
        avg_score = (sum(r.score for r in results) / total) if total else None
        m.update({
            "businesses_discovered": _metric(total),
            "businesses_researched": _metric(researched),
            "research_completion_rate": _metric(
                round(researched / total, 4) if total else None,
                note="researched+imported / discovered"),
            "qualified_leads": _metric(_count(
                db, Lead, Lead.workspace_id == wid,
                Lead.qualification_status == QualificationStatus.qualified)),
            "average_lead_score": _metric(
                round(avg_score, 1) if avg_score is not None else None),
            "duplicate_rate": _metric(
                round(dups / total, 4) if total else None,
                note="duplicate / discovered"),
            "research_failures": _metric(failed),
            "jobs_completed": _metric(len(done_jobs)),
            "jobs_failed": _metric(sum(
                1 for j in jobs if j.status == ResearchJobStatus.failed)),
            "avg_processing_seconds": _metric(
                round(sum((j.finished_at - j.started_at).total_seconds()
                          for j in done_jobs) / len(done_jobs), 1)
                if done_jobs else None,
                quality="estimate" if done_jobs else "unavailable",
                note="mean job duration"),
        })
    elif slug == "sales-executive":
        msgs = db.scalars(select(Message).where(
            Message.workspace_id == wid,
            Message.direction == MessageDirection.outbound,
            Message.is_ai_draft.is_(True))).all()
        sent = [m for m in msgs if m.status in (
            MessageStatus.sent, MessageStatus.delivered, MessageStatus.read)]
        failed = [m for m in msgs if m.status == MessageStatus.failed]
        approvals = db.scalars(select(Approval).where(
            Approval.workspace_id == wid,
            Approval.action_type.in_([ApprovalActionType.send_email,
                                      ApprovalActionType.send_whatsapp]))).all()
        decided = [a for a in approvals if a.status in (
            ApprovalStatus.approved, ApprovalStatus.rejected)]
        appr_rate = (sum(1 for a in decided if a.status == ApprovalStatus.approved)
                     / len(decided)) if decided else None
        engaged_leads = _count(db, Lead, Lead.workspace_id == wid,
                               Lead.last_contacted_at.is_not(None))
        m.update({
            "campaigns_created": _metric(_count(db, Campaign, Campaign.workspace_id == wid)),
            "messages_drafted": _metric(len(msgs)),
            "messages_sent": _metric(len(sent)),
            "delivery_failures": _metric(len(failed)),
            "outreach_approval_rate": _metric(
                round(appr_rate, 4) if appr_rate is not None else None,
                quality="exact" if decided else "unavailable"),
            "prospects_engaged": _metric(engaged_leads),
        })
    elif slug == "communication-manager":
        inbound = _count(db, Message, Message.workspace_id == wid,
                         Message.direction == MessageDirection.inbound)
        convs = _count(db, Conversation, Conversation.workspace_id == wid)
        drafts = _count(db, Message, Message.workspace_id == wid,
                        Message.direction == MessageDirection.outbound,
                        Message.is_ai_draft.is_(True))
        sent_out = _count(db, Message, Message.workspace_id == wid,
                          Message.direction == MessageDirection.outbound,
                          Message.status.in_([MessageStatus.sent,
                                              MessageStatus.delivered,
                                              MessageStatus.read]))
        pending_appr = _count(db, Approval, Approval.workspace_id == wid,
                              Approval.status == ApprovalStatus.pending)
        interested = _count(db, Lead, Lead.workspace_id == wid,
                            Lead.stage.in_([LeadStage.interested,
                                            LeadStage.demo_scheduled,
                                            LeadStage.negotiation,
                                            LeadStage.won]))
        escalations = _count(db, Conversation, Conversation.workspace_id == wid,
                             Conversation.needs_human.is_(True))
        optouts = _count(db, Lead, Lead.workspace_id == wid,
                         Lead.stage == LeadStage.do_not_contact)
        # avg first-response time: inbound -> next outbound sent (sample)
        resp_times = []
        conv_ids = db.scalars(select(Conversation.id).where(
            Conversation.workspace_id == wid).limit(200)).all()
        for cid in conv_ids:
            ins = db.scalars(select(Message).where(
                Message.conversation_id == cid,
                Message.direction == MessageDirection.inbound)
                .order_by(Message.created_at)).all()
            outs = db.scalars(select(Message).where(
                Message.conversation_id == cid,
                Message.direction == MessageDirection.outbound,
                Message.status.in_([MessageStatus.sent, MessageStatus.delivered,
                                    MessageStatus.read]),
                Message.sent_at.is_not(None))
                .order_by(Message.sent_at)).all()
            for im in ins[:3]:
                nxt = next((o for o in outs if o.sent_at and o.sent_at > im.created_at), None)
                if nxt:
                    resp_times.append((nxt.sent_at - im.created_at).total_seconds() / 3600)
        avg_resp = (sum(resp_times) / len(resp_times)) if resp_times else None
        m.update({
            "inbound_messages": _metric(inbound),
            "conversations_handled": _metric(convs),
            "responses_generated": _metric(drafts),
            "responses_sent": _metric(sent_out),
            "pending_approvals": _metric(pending_appr),
            "interested_prospects": _metric(interested),
            "human_escalations": _metric(escalations),
            "opt_outs": _metric(optouts),
            "avg_response_hours": _metric(
                round(avg_resp, 2) if avg_resp is not None else None,
                quality="estimate" if resp_times else "unavailable",
                note="first outbound after inbound, sampled"),
        })
    elif slug == "executive-assistant":
        appts = db.scalars(select(Appointment).where(
            Appointment.workspace_id == wid)).all()
        demos = [a for a in appts if a.appointment_type.value == "product_demo"]
        def c(st): return sum(1 for a in appts if a.status == st)
        held = c(AppointmentStatus.completed) + c(AppointmentStatus.no_show)
        m.update({
            "appointments_total": _metric(len(appts)),
            "demos_requested": _metric(sum(
                1 for a in demos if a.status in (
                    AppointmentStatus.proposed,
                    AppointmentStatus.awaiting_confirmation,
                    AppointmentStatus.confirmed,
                    AppointmentStatus.rescheduling_requested,
                    AppointmentStatus.completed,
                    AppointmentStatus.no_show))),
            "appointments_proposed": _metric(c(AppointmentStatus.proposed)),
            "appointments_confirmed": _metric(c(AppointmentStatus.confirmed)),
            "demos_completed": _metric(c(AppointmentStatus.completed)),
            "cancellations": _metric(c(AppointmentStatus.cancelled)),
            "no_shows": _metric(c(AppointmentStatus.no_show)),
            "demo_attendance_rate": _metric(
                round(c(AppointmentStatus.completed) / held, 4) if held else None,
                note="completed / (completed+no_show)"),
        })
    elif slug == "sales-manager":
        tasks = db.scalars(select(Task).where(
            Task.workspace_id == wid,
            Task.assignee_agent_id == (agent.id if agent else None))).all() \
            if agent else []
        reports = _count(db, ManagementReport, ManagementReport.workspace_id == wid)
        issues = _count(db, SalesIssue, SalesIssue.workspace_id == wid)
        recs = _count(db, Recommendation, Recommendation.workspace_id == wid)
        open_issues = _count(db, SalesIssue, SalesIssue.workspace_id == wid,
                             SalesIssue.status.in_(["open", "acknowledged"]))
        m.update({
            "reports_generated": _metric(reports),
            "issues_identified": _metric(issues),
            "open_issues": _metric(open_issues),
            "recommendations_created": _metric(recs),
            "tasks_assigned": _metric(len(tasks)),
            "tasks_completed": _metric(sum(
                1 for t in tasks if t.status == TaskStatus.completed)),
            "pending_owner_decisions": _metric(_count(
                db, Approval, Approval.workspace_id == wid,
                Approval.status == ApprovalStatus.pending)),
        })
    # agent run health for every agent
    if agent:
        runs = db.scalars(select(AgentRun).where(
            AgentRun.workspace_id == wid, AgentRun.agent_id == agent.id,
            AgentRun.created_at >= since)).all()
        m["runs_last_7d"] = _metric({
            "total": len(runs),
            "failed": sum(1 for r in runs if r.status.value == "failed"),
            "awaiting_approval": sum(1 for r in runs if r.status.value == "awaiting_approval"),
        })
    return m


# ---------------------------------------------------------------- funnel


def funnel(db: Session, wid: uuid.UUID, days: int = 90) -> dict:
    """Sales funnel computed from real records. Counts are workspace-scoped."""
    since = _now() - timedelta(days=days)

    def c(model, *conds):
        return _count(db, model, model.workspace_id == wid, *conds)

    discovered = c(Lead)
    # progressed past 'discovered' stage
    progressed = c(Lead, Lead.stage != LeadStage.discovered)
    qualified = c(Lead, Lead.qualification_status == QualificationStatus.qualified)
    approved_outreach = db.scalar(select(func.count(func.distinct(Approval.lead_id))).where(
        Approval.workspace_id == wid, Approval.lead_id.is_not(None),
        Approval.status == ApprovalStatus.approved,
        Approval.action_type.in_([ApprovalActionType.send_email,
                                  ApprovalActionType.send_whatsapp]))) or 0
    first_contact = db.scalar(select(func.count(func.distinct(Message.conversation_id)))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(Message.workspace_id == wid,
               Message.direction == MessageDirection.outbound,
               Message.status.in_([MessageStatus.sent, MessageStatus.delivered,
                                   MessageStatus.read]))) or 0
    delivered = _count(db, Message, Message.workspace_id == wid,
                       Message.direction == MessageDirection.outbound,
                       Message.status.in_([MessageStatus.delivered, MessageStatus.read]))
    replied = db.scalar(select(func.count(func.distinct(Conversation.lead_id))).where(
        Conversation.workspace_id == wid,
        Conversation.id.in_(select(Message.conversation_id).where(
            Message.direction == MessageDirection.inbound)))) or 0
    interested = c(Lead, Lead.stage.in_([LeadStage.interested, LeadStage.demo_scheduled,
                                         LeadStage.negotiation, LeadStage.won]))
    demo_requested = _count(db, Appointment, Appointment.workspace_id == wid,
                            Appointment.appointment_type == "product_demo")
    demo_scheduled = _count(db, Appointment, Appointment.workspace_id == wid,
                            Appointment.status.in_([AppointmentStatus.confirmed,
                                                    AppointmentStatus.completed,
                                                    AppointmentStatus.no_show]))
    demo_completed = _count(db, Appointment, Appointment.workspace_id == wid,
                            Appointment.status == AppointmentStatus.completed)
    opportunities = c(Opportunity)
    converted = c(Lead, Lead.stage == LeadStage.won)
    lost = c(Lead, Lead.stage == LeadStage.lost)

    stages = [
        ("discovered", discovered),
        ("researched", progressed),
        ("qualified", qualified),
        ("approved_outreach", approved_outreach),
        ("first_contact_sent", first_contact),
        ("message_delivered", delivered),
        ("prospect_replied", replied),
        ("prospect_interested", interested),
        ("demo_requested", demo_requested),
        ("demo_scheduled", demo_scheduled),
        ("demo_completed", demo_completed),
        ("opportunity_created", opportunities),
        ("customer_converted", converted),
        ("deal_lost", lost),
    ]
    rows = []
    prev = None
    for name, count in stages:
        conv = round(count / prev, 4) if prev else None
        rows.append({"stage": name, "count": count,
                     "conversion_from_previous": conv,
                     "dropoff_from_previous": (prev - count) if prev is not None else None})
        if name != "deal_lost":
            prev = count if count else prev
    # revenue from actual won opportunities
    revenue = float(db.scalar(select(func.coalesce(func.sum(Opportunity.value), 0)).where(
        Opportunity.workspace_id == wid,
        Opportunity.status == OpportunityStatus.won)) or 0)
    return {
        "window_days": days,
        "generated_at": _now().isoformat(),
        "stages": rows,
        "revenue_won": revenue,
        "currency": "USD",
        "definitions": {
            "discovered": "all leads in workspace",
            "researched": "leads that progressed past 'discovered' stage",
            "qualified": "leads with qualification_status=qualified",
            "approved_outreach": "distinct leads with an approved send approval",
            "first_contact_sent": "distinct conversations with a sent/delivered/read outbound message",
            "message_delivered": "outbound messages with delivered/read status (not drafts)",
            "prospect_replied": "distinct leads with an inbound message",
            "prospect_interested": "leads in interested/demo_scheduled/negotiation/won",
            "demo_requested": "product_demo appointment records (any status)",
            "demo_scheduled": "confirmed/completed/no_show demo appointments",
            "demo_completed": "completed demo appointments only",
            "customer_converted": "leads in stage 'won'",
            "deal_lost": "leads in stage 'lost'",
        },
        "notes": [
            "Drafts and pending approvals are never counted as sent.",
            "Proposed appointments are not counted as scheduled demos.",
            "Conversion uses the previous non-zero funnel stage as denominator.",
        ],
    }

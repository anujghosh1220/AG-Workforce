from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth
from app.core.database import get_db
from app.models import (
    AgentRun,
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Approval,
    ApprovalStatus,
    Channel,
    Lead,
    LeadStage,
    Message,
    MessageStatus,
    QualificationStatus,
    Task,
)
from app.schemas import AnalyticsOverview

router = APIRouter(prefix="/analytics", tags=["analytics"])

WON_STAGES = {LeadStage.won}
INTERESTED_STAGES = {LeadStage.interested, LeadStage.demo_scheduled, LeadStage.negotiation, LeadStage.won}
DEMO_STAGES = {LeadStage.demo_scheduled, LeadStage.negotiation, LeadStage.won}


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


@router.get("/overview", response_model=AnalyticsOverview)
def overview(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    wid = auth.workspace.id
    now = datetime.now(timezone.utc)
    since_30d = now - timedelta(days=30)

    # ---- leads
    total_leads = db.scalar(select(func.count(Lead.id)).where(Lead.workspace_id == wid)) or 0
    stage_rows = db.execute(
        select(Lead.stage, func.count(Lead.id)).where(Lead.workspace_id == wid).group_by(Lead.stage)
    ).all()
    leads_by_stage = {s.value: c for s, c in stage_rows}

    source_rows = db.execute(
        select(Lead.source, func.count(Lead.id)).where(Lead.workspace_id == wid).group_by(Lead.source)
    ).all()
    leads_by_source = {s: c for s, c in source_rows}

    qualified = db.scalar(
        select(func.count(Lead.id)).where(
            Lead.workspace_id == wid, Lead.qualification_status == QualificationStatus.qualified
        )
    ) or 0

    interested = db.scalar(
        select(func.count(Lead.id)).where(Lead.workspace_id == wid, Lead.stage.in_(INTERESTED_STAGES))
    ) or 0
    # Phase 6 audit: booked/completed meetings come from confirmed/completed
    # appointment records, not inferred lead stages.
    from app.models import Appointment as _Appt, AppointmentStatus as _AS
    meetings_booked = db.scalar(
        select(func.count(_Appt.id)).where(
            _Appt.workspace_id == wid,
            _Appt.status.in_([_AS.confirmed, _AS.completed, _AS.no_show]))
    ) or 0
    meetings_completed = db.scalar(
        select(func.count(_Appt.id)).where(
            _Appt.workspace_id == wid, _Appt.status == _AS.completed)
    ) or 0
    demo_requests = db.scalar(
        select(func.count(Lead.id)).where(Lead.workspace_id == wid, Lead.stage.in_(DEMO_STAGES))
    ) or 0
    won = db.scalar(
        select(func.count(Lead.id)).where(Lead.workspace_id == wid, Lead.stage.in_(WON_STAGES))
    ) or 0

    closed_stages = [LeadStage.won, LeadStage.lost, LeadStage.do_not_contact]
    pipeline_value = float(
        db.scalar(
            select(func.coalesce(func.sum(Lead.estimated_value), 0)).where(
                Lead.workspace_id == wid, ~Lead.stage.in_(closed_stages)
            )
        ) or 0
    )
    won_value = float(
        db.scalar(
            select(func.coalesce(func.sum(Lead.estimated_value), 0)).where(
                Lead.workspace_id == wid, Lead.stage == LeadStage.won
            )
        ) or 0
    )
    from app.models import Opportunity as _Opp, OpportunityStatus as _OS
    revenue_won = float(
        db.scalar(select(func.coalesce(func.sum(_Opp.value), 0)).where(
            _Opp.workspace_id == wid, _Opp.status == _OS.won)) or 0
    )

    # ---- messages
    msg_rows = db.execute(
        select(Message.channel, func.count(Message.id)).where(
            Message.workspace_id == wid, Message.direction == "outbound",
            Message.status.in_([MessageStatus.sent, MessageStatus.delivered, MessageStatus.read]),
        ).group_by(Message.channel)
    ).all()
    sent_by_channel = {c.value: n for c, n in msg_rows}
    emails_sent = sent_by_channel.get(Channel.email.value, 0)
    whatsapp_sent = sent_by_channel.get(Channel.whatsapp.value, 0)

    inbound = db.scalar(
        select(func.count(Message.id)).where(
            Message.workspace_id == wid, Message.direction == "inbound"
        )
    ) or 0
    contacted_leads = db.scalar(
        select(func.count(func.distinct(Lead.id))).where(
            Lead.workspace_id == wid, Lead.last_contacted_at.is_not(None)
        )
    ) or 0

    # ---- tasks / approvals
    task_rows = db.execute(
        select(Task.status, func.count(Task.id)).where(Task.workspace_id == wid).group_by(Task.status)
    ).all()
    tasks_by_status = {s.value: c for s, c in task_rows}
    pending_approvals = db.scalar(
        select(func.count(Approval.id)).where(
            Approval.workspace_id == wid, Approval.status == ApprovalStatus.pending
        )
    ) or 0

    # ---- agent activity
    run_rows = db.execute(
        select(AgentRun.status, func.count(AgentRun.id), func.coalesce(func.sum(AgentRun.tokens_prompt + AgentRun.tokens_completion), 0))
        .where(AgentRun.workspace_id == wid)
        .group_by(AgentRun.status)
    ).all()
    agent_activity = [
        {"status": s.value, "runs": c, "tokens": int(t)} for s, c, t in run_rows
    ]

    # ---- time series (last 30 days)
    lead_days = db.execute(
        select(func.date(Lead.created_at), func.count(Lead.id))
        .where(Lead.workspace_id == wid, Lead.created_at >= since_30d)
        .group_by(func.date(Lead.created_at))
        .order_by(func.date(Lead.created_at))
    ).all()
    leads_over_time = [{"date": str(d), "count": c} for d, c in lead_days]

    msg_days = db.execute(
        select(func.date(Message.created_at), func.count(Message.id))
        .where(Message.workspace_id == wid, Message.created_at >= since_30d)
        .group_by(func.date(Message.created_at))
        .order_by(func.date(Message.created_at))
    ).all()
    messages_over_time = [{"date": str(d), "count": c} for d, c in msg_days]

    # ---- appointment metrics (Phase 5) — real appointment records only
    appt_rows = db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(Appointment.workspace_id == wid)
        .group_by(Appointment.status)
    ).all()
    appt_by_status = {s.value: c for s, c in appt_rows}
    demo_type = AppointmentType.product_demo
    demo_rows = db.execute(
        select(Appointment.status, func.count(Appointment.id))
        .where(Appointment.workspace_id == wid,
               Appointment.appointment_type == demo_type)
        .group_by(Appointment.status)
    ).all()
    demos_by_status = {s.value: c for s, c in demo_rows}

    demos_requested = appt_by_status.get(AppointmentStatus.proposed.value, 0)
    demos_proposed = sum(
        appt_by_status.get(s.value, 0)
        for s in (AppointmentStatus.proposed, AppointmentStatus.awaiting_confirmation,
                  AppointmentStatus.confirmed, AppointmentStatus.rescheduling_requested,
                  AppointmentStatus.completed, AppointmentStatus.no_show)
    )
    demos_booked = appt_by_status.get(AppointmentStatus.confirmed.value, 0) + \
        appt_by_status.get(AppointmentStatus.completed.value, 0) + \
        appt_by_status.get(AppointmentStatus.no_show.value, 0)
    demos_completed = appt_by_status.get(AppointmentStatus.completed.value, 0)
    demos_cancelled = appt_by_status.get(AppointmentStatus.cancelled.value, 0)
    demo_no_shows = appt_by_status.get(AppointmentStatus.no_show.value, 0)
    demo_held = demos_completed + demo_no_shows
    # demo->customer: leads with a completed demo that are now won
    demo_won = db.scalar(
        select(func.count(func.distinct(Appointment.lead_id)))
        .join(Lead, Lead.id == Appointment.lead_id)
        .where(Appointment.workspace_id == wid,
               Appointment.appointment_type == demo_type,
               Appointment.status == AppointmentStatus.completed,
               Lead.stage == LeadStage.won)
    ) or 0
    demo_leads_total = db.scalar(
        select(func.count(func.distinct(Appointment.lead_id)))
        .where(Appointment.workspace_id == wid,
               Appointment.appointment_type == demo_type,
               Appointment.status == AppointmentStatus.completed)
    ) or 0

    return AnalyticsOverview(
        total_leads=total_leads,
        leads_by_stage=leads_by_stage,
        leads_by_source=leads_by_source,
        qualified_leads=qualified,
        emails_sent=emails_sent,
        whatsapp_sent=whatsapp_sent,
        interested_prospects=interested,
        demo_requests=demo_requests,
        won_deals=won,
        messages_received=inbound,
        pipeline_value=pipeline_value,
        won_value=won_value,
        tasks_by_status=tasks_by_status,
        pending_approvals=pending_approvals,
        meetings_booked=meetings_booked,
        meetings_completed=meetings_completed,
        revenue_won=revenue_won,
        response_rate=_rate(inbound, emails_sent + whatsapp_sent),
        qualification_rate=_rate(qualified, total_leads),
        conversion_rate=_rate(won, total_leads),
        leads_over_time=leads_over_time,
        messages_over_time=messages_over_time,
        agent_activity=agent_activity,
        demos_requested=demos_requested,
        demos_proposed=demos_proposed,
        demos_booked=demos_booked,
        demos_completed=demos_completed,
        demos_cancelled=demos_cancelled,
        demo_no_shows=demo_no_shows,
        demo_attendance_rate=_rate(demos_completed, demo_held),
        demo_to_customer_rate=_rate(demo_won, demo_leads_total),
    )


# ------------------------------------------------------------ LLM usage (WS7)


@router.get("/llm-usage")
def llm_usage(days: int = Query(30, ge=1, le=365),
              auth: AuthContext = Depends(get_current_auth),
              db: Session = Depends(get_db)):
    """Actual provider-call metering for the caller's workspace.
    Never accepts a workspace id from the client."""
    from datetime import timedelta
    from sqlalchemy import func as _f
    from app.models import LLMInvocation
    from app.services import llm_metering

    wid = auth.workspace.id
    since = datetime.now(timezone.utc) - timedelta(days=days)

    def _agg(*cols):
        return db.execute(
            select(*cols).where(
                LLMInvocation.workspace_id == wid,
                LLMInvocation.created_at >= since)).one()

    total, tp, tc, failed = _agg(
        _f.count(LLMInvocation.id),
        _f.coalesce(_f.sum(LLMInvocation.tokens_prompt), 0),
        _f.coalesce(_f.sum(LLMInvocation.tokens_completion), 0),
        _f.coalesce(_f.sum(_f.case((LLMInvocation.status == "failed", 1),
                                    else_=0)), 0))
    # by provider/model
    by_model = db.execute(
        select(LLMInvocation.provider, LLMInvocation.model,
               _f.count(LLMInvocation.id),
               _f.coalesce(_f.sum(LLMInvocation.tokens_prompt), 0),
               _f.coalesce(_f.sum(LLMInvocation.tokens_completion), 0))
        .where(LLMInvocation.workspace_id == wid,
               LLMInvocation.created_at >= since)
        .group_by(LLMInvocation.provider, LLMInvocation.model)).all()
    by_agent = db.execute(
        select(LLMInvocation.agent_slug,
               _f.count(LLMInvocation.id))
        .where(LLMInvocation.workspace_id == wid,
               LLMInvocation.created_at >= since)
        .group_by(LLMInvocation.agent_slug)).all()
    daily = db.execute(
        select(_f.date(LLMInvocation.created_at),
               _f.count(LLMInvocation.id))
        .where(LLMInvocation.workspace_id == wid,
               LLMInvocation.created_at >= since)
        .group_by(_f.date(LLMInvocation.created_at))
        .order_by(_f.date(LLMInvocation.created_at))).all()
    return {
        "workspace_id": str(wid),
        "window_days": days,
        "calls": int(total),
        "failed_calls": int(failed),
        "tokens_prompt": int(tp),
        "tokens_completion": int(tc),
        "note": "token counts are provider-reported; 0 means the provider "
                "did not return usage metadata",
        "by_model": [{"provider": p, "model": m, "calls": int(c),
                      "tokens_prompt": int(a), "tokens_completion": int(b)}
                     for p, m, c, a, b in by_model],
        "by_agent": [{"agent": s or "unattributed", "calls": int(c)}
                     for s, c in by_agent],
        "daily": [{"date": str(d), "calls": int(c)} for d, c in daily],
    }

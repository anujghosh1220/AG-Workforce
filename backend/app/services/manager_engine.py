"""Phase 6 — bottleneck detection, recommendations, reports, intelligence.

Rule-based detection produces SalesIssue rows idempotently (dedupe by
workspace+issue_type+fingerprint). Recommendations derive from issues and
positive signals. Reports aggregate real records. LLM insights cite the
same metrics and fall back to honest heuristic text when no LLM exists.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.email.base import get_email_provider
from app.integrations.whatsapp.base import get_whatsapp_provider
from app.models import (
    Agent, AgentRun, Appointment, AppointmentStatus, Approval, ApprovalStatus,
    Campaign, Channel, Conversation, FollowUp, FollowUpStatus, IssueSeverity,
    IssueStatus, Lead, LeadStage, ManagementReport, Message, MessageDirection,
    MessageStatus, NotificationType, Opportunity, OpportunityStatus,
    Recommendation, RecommendationStatus, ReportType, ResearchJob,
    ResearchJobStatus, ResearchResult, ResearchResultStatus, SalesIssue,
    Task, TaskPriority, TaskStatus, AnalysisRun, AnalysisRunStatus,
)
from app.services import manager_metrics
from app.services.helpers import log_activity, notify_workspace


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _count(db, model, *conds):
    return db.scalar(select(func.count(model.id)).where(*conds)) or 0


# ---------------------------------------------------------------- issues


def _upsert_issue(db, wid, issue_type, fingerprint, severity, title, detail,
                  agent_slug=None, workflow=None, evidence=None,
                  resolution="") -> SalesIssue:
    existing = db.scalar(select(SalesIssue).where(
        SalesIssue.workspace_id == wid,
        SalesIssue.issue_type == issue_type,
        SalesIssue.fingerprint == fingerprint,
        SalesIssue.status.in_([IssueStatus.open, IssueStatus.acknowledged]),
    ))
    if existing:
        existing.last_detected_at = _now()
        existing.evidence = evidence or existing.evidence
        if severity.value != "informational":
            existing.severity = severity
        return existing
    issue = SalesIssue(
        workspace_id=wid, issue_type=issue_type, fingerprint=fingerprint,
        severity=severity, title=title, detail=detail, agent_slug=agent_slug,
        workflow=workflow, evidence=evidence or {},
        suggested_resolution=resolution,
    )
    db.add(issue)
    db.flush()
    return issue


def detect_bottlenecks(db: Session, wid: uuid.UUID) -> list[SalesIssue]:
    """Run all rules; return newly created or refreshed issues."""
    issues = []
    now = _now()
    d7 = now - timedelta(days=7)

    # 1. low qualification rate
    total = _count(db, Lead, Lead.workspace_id == wid)
    qual = _count(db, Lead, Lead.workspace_id == wid,
                  Lead.qualification_status == "qualified")
    if total >= 20 and (qual / total) < 0.15:
        issues.append(_upsert_issue(
            db, wid, "low_qualification_rate", "global", IssueSeverity.medium,
            "Low lead qualification rate",
            f"Only {qual}/{total} leads ({qual/total:.0%}) are qualified.",
            agent_slug="lead-researcher", workflow="research",
            evidence={"total_leads": total, "qualified": qual,
                      "rate": round(qual / total, 4)},
            resolution="Review research targeting and qualification criteria; "
                       "expand research in higher-converting areas."))

    # 2. pending approvals backlog
    pending = _count(db, Approval, Approval.workspace_id == wid,
                     Approval.status == ApprovalStatus.pending)
    if pending >= 10:
        issues.append(_upsert_issue(
            db, wid, "approval_backlog", "global", IssueSeverity.medium,
            "Approval backlog growing",
            f"{pending} approvals are waiting for owner review.",
            workflow="approvals",
            evidence={"pending_approvals": pending},
            resolution="Review the approvals queue; consider adjusting agent "
                       "autonomy settings for routine actions."))

    # 3. provider availability
    if get_email_provider() is None:
        issues.append(_upsert_issue(
            db, wid, "email_provider_unavailable", "global",
            IssueSeverity.informational,
            "Email provider not configured",
            "Outbound email and email reminders cannot be delivered externally; "
            "they are retained as internal notifications.",
            workflow="email", evidence={"provider": "resend"},
            resolution="Configure RESEND_API_KEY and EMAIL_FROM to enable "
                       "external email delivery."))
    if get_whatsapp_provider() is None:
        issues.append(_upsert_issue(
            db, wid, "whatsapp_provider_unavailable", "global",
            IssueSeverity.informational,
            "WhatsApp provider not configured",
            "WhatsApp sends and reminders cannot be delivered externally.",
            workflow="whatsapp", evidence={"provider": "meta_cloud"},
            resolution="Configure WHATSAPP_ACCESS_TOKEN and "
                       "WHATSAPP_PHONE_NUMBER_ID."))

    # 4. stalled conversations (>72h since last inbound, no newer outbound)
    stalled = 0
    convs = db.scalars(select(Conversation).where(
        Conversation.workspace_id == wid,
        Conversation.state.notin_(["opted_out", "closed", "human_handoff",
                                   "demo_scheduled"])).limit(500)).all()
    for conv in convs:
        last_in = db.scalar(select(func.max(Message.created_at)).where(
            Message.conversation_id == conv.id,
            Message.direction == MessageDirection.inbound))
        if not last_in or last_in > now - timedelta(hours=72):
            continue
        last_out = db.scalar(select(func.max(Message.created_at)).where(
            Message.conversation_id == conv.id,
            Message.direction == MessageDirection.outbound))
        if last_out is None or last_out < last_in:
            stalled += 1
    if stalled >= 5:
        issues.append(_upsert_issue(
            db, wid, "stalled_conversations", "global", IssueSeverity.medium,
            "Conversations stalled without response",
            f"{stalled} conversations have an unanswered inbound message "
            "older than 72 hours.",
            agent_slug="communication-manager", workflow="conversations",
            evidence={"stalled_count": stalled, "threshold_hours": 72},
            resolution="Review stalled threads and assign follow-up drafts."))

    # 5. demo no-show / cancellation rates
    held = _count(db, Appointment, Appointment.workspace_id == wid,
                  Appointment.status.in_([AppointmentStatus.completed,
                                          AppointmentStatus.no_show]))
    no_shows = _count(db, Appointment, Appointment.workspace_id == wid,
                      Appointment.status == AppointmentStatus.no_show)
    if held >= 5 and (no_shows / held) > 0.3:
        issues.append(_upsert_issue(
            db, wid, "high_no_show_rate", "demos", IssueSeverity.medium,
            "High demo no-show rate",
            f"{no_shows}/{held} demos ({no_shows/held:.0%}) ended as no-shows.",
            agent_slug="executive-assistant", workflow="appointments",
            evidence={"no_shows": no_shows, "held": held,
                      "rate": round(no_shows / held, 4)},
            resolution="Enable reminder confirmations, send pre-demo briefs, "
                       "and consider confirmation calls for high-value demos."))
    cancels = _count(db, Appointment, Appointment.workspace_id == wid,
                     Appointment.status == AppointmentStatus.cancelled)
    booked = _count(db, Appointment, Appointment.workspace_id == wid,
                    Appointment.status.in_([AppointmentStatus.confirmed,
                                            AppointmentStatus.completed,
                                            AppointmentStatus.no_show,
                                            AppointmentStatus.cancelled]))
    if booked >= 5 and (cancels / booked) > 0.4:
        issues.append(_upsert_issue(
            db, wid, "high_cancellation_rate", "demos", IssueSeverity.low,
            "High appointment cancellation rate",
            f"{cancels}/{booked} appointments ({cancels/booked:.0%}) were cancelled.",
            agent_slug="executive-assistant", workflow="appointments",
            evidence={"cancelled": cancels, "total": booked,
                      "rate": round(cancels / booked, 4)},
            resolution="Review slot quality and reminder cadence."))

    # 6. research job failures in last 7 days
    failed_jobs = _count(db, ResearchJob, ResearchJob.workspace_id == wid,
                         ResearchJob.status == ResearchJobStatus.failed,
                         ResearchJob.updated_at >= d7)
    if failed_jobs >= 3:
        issues.append(_upsert_issue(
            db, wid, "research_worker_failures", "research", IssueSeverity.high,
            "Research worker repeatedly failing",
            f"{failed_jobs} research jobs failed in the last 7 days.",
            agent_slug="lead-researcher", workflow="research",
            evidence={"failed_jobs_7d": failed_jobs},
            resolution="Inspect recent research job errors and provider "
                       "configuration."))

    # 7. agent run failures
    run_rows = db.execute(select(AgentRun.agent_id, func.count(AgentRun.id)).where(
        AgentRun.workspace_id == wid, AgentRun.status == "failed",
        AgentRun.created_at >= d7).group_by(AgentRun.agent_id)).all()
    agent_names = {a.id: a.slug for a in db.scalars(select(Agent).where(
        Agent.workspace_id == wid)).all()}
    for agent_id, n in run_rows:
        if n >= 3:
            slug = agent_names.get(agent_id, "unknown")
            issues.append(_upsert_issue(
                db, wid, "agent_run_failures", slug, IssueSeverity.medium,
                f"{slug} has repeated execution failures",
                f"{n} failed runs in the last 7 days.",
                agent_slug=slug, workflow="agent_runs",
                evidence={"failed_runs_7d": n},
                resolution="Review the agent's recent run errors."))

    # 8. leads missing contact info
    missing = _count(db, Lead, Lead.workspace_id == wid,
                     (Lead.email.is_(None)) & (Lead.phone.is_(None)))
    if total >= 20 and (missing / total) > 0.4:
        issues.append(_upsert_issue(
            db, wid, "missing_contact_info", "crm", IssueSeverity.low,
            "Many leads lack contact information",
            f"{missing}/{total} leads ({missing/total:.0%}) have neither email "
            "nor phone — they cannot be contacted.",
            workflow="crm",
            evidence={"missing": missing, "total": total,
                      "rate": round(missing / total, 4)},
            resolution="Prioritize research/enrichment for leads without "
                       "contact details."))

    # 9. follow-up failures
    fu_failed = _count(db, FollowUp, FollowUp.workspace_id == wid,
                       FollowUp.status == FollowUpStatus.failed,
                       FollowUp.updated_at >= d7)
    if fu_failed >= 5:
        issues.append(_upsert_issue(
            db, wid, "followup_failures", "comms", IssueSeverity.medium,
            "Follow-up sends repeatedly failing",
            f"{fu_failed} follow-ups failed in the last 7 days.",
            agent_slug="communication-manager", workflow="follow_ups",
            evidence={"failed_followups_7d": fu_failed},
            resolution="Check channel provider status and consent records."))

    # 10. campaigns with zero engagement
    for camp in db.scalars(select(Campaign).where(
            Campaign.workspace_id == wid,
            Campaign.status == "completed")).all():
        stats = camp.stats or {}
        sent_n = stats.get("sent", 0)
        if sent_n and stats.get("replies", 0) == 0 and sent_n >= 10:
            issues.append(_upsert_issue(
                db, wid, "campaign_no_engagement", str(camp.id),
                IssueSeverity.low,
                f"Campaign '{camp.name}' produced no replies",
                f"{sent_n} messages sent, 0 replies recorded.",
                agent_slug="sales-executive", workflow="campaigns",
                evidence={"campaign_id": str(camp.id), "sent": sent_n,
                          "replies": 0},
                resolution="Review message copy and targeting for this campaign."))

    db.flush()
    return issues


# ---------------------------------------------------------------- recommendations


def generate_recommendations(db: Session, wid: uuid.UUID) -> list[Recommendation]:
    """Create recommendations from open issues and positive funnel signals."""
    created = []

    def add(title, explanation, benefit, priority, action, evidence,
            confidence="medium", issue=None, task_spec=None, related=None):
        # idempotent: skip if an open/new rec with same title exists
        exists = db.scalar(select(Recommendation.id).where(
            Recommendation.workspace_id == wid,
            Recommendation.title == title,
            Recommendation.status.in_([RecommendationStatus.new,
                                       RecommendationStatus.accepted]),
        ))
        if exists:
            return None
        rec = Recommendation(
            workspace_id=wid, title=title, explanation=explanation,
            expected_benefit=benefit, priority=priority,
            suggested_action=action, supporting_data=evidence,
            confidence=confidence, source="rules",
            task_spec=task_spec or {}, related_records=related or {},
            issue_id=issue.id if issue else None,
        )
        db.add(rec)
        created.append(rec)
        return rec

    issues = db.scalars(select(SalesIssue).where(
        SalesIssue.workspace_id == wid,
        SalesIssue.status.in_([IssueStatus.open, IssueStatus.acknowledged]))).all()
    for issue in issues:
        sev_pri = {IssueSeverity.critical: TaskPriority.urgent,
                   IssueSeverity.high: TaskPriority.high,
                   IssueSeverity.medium: TaskPriority.medium,
                   IssueSeverity.low: TaskPriority.low,
                   IssueSeverity.informational: TaskPriority.low}[issue.severity]
        add(
            title=f"Address: {issue.title}",
            explanation=issue.detail,
            benefit=issue.suggested_resolution,
            priority=sev_pri,
            action=issue.suggested_resolution,
            evidence=issue.evidence,
            confidence="high" if issue.evidence else "medium",
            issue=issue,
            task_spec={"agent_slug": issue.agent_slug or "sales-manager",
                       "title": f"Investigate: {issue.title}",
                       "description": issue.detail})

    # positive signal: best converting city -> suggest more research there
    city_rows = db.execute(select(Lead.city, func.count(Lead.id)).where(
        Lead.workspace_id == wid, Lead.city.is_not(None),
        Lead.stage.in_([LeadStage.interested, LeadStage.demo_scheduled,
                        LeadStage.negotiation, LeadStage.won]))
        .group_by(Lead.city).order_by(func.count(Lead.id).desc()).limit(1)).all()
    if city_rows and city_rows[0][0]:
        city, n = city_rows[0]
        add(
            title=f"Double down on {city}",
            explanation=f"{city} produces the most interested/converted leads "
                        f"({n} leads in advanced stages).",
            benefit="More qualified pipeline from a proven location.",
            priority=TaskPriority.medium,
            action=f"Run a Lead Research job focused on cafés in {city}.",
            evidence={"city": city, "advanced_leads": n},
            confidence="medium",
            task_spec={"agent_slug": "lead-researcher",
                       "title": f"Research more cafés in {city}",
                       "description": f"{city} shows strong conversion; expand "
                                      "research coverage there."})

    db.flush()
    return created


# ---------------------------------------------------------------- reports


def _report_payload(db, wid, period_start, period_end) -> dict:
    """Aggregate real metrics for a reporting window."""
    p = period_start
    q = period_end

    def c(model, *conds):
        return _count(db, model, model.workspace_id == wid,
                      model.created_at >= p, model.created_at < q, *conds)

    new_leads = c(Lead)
    qualified = c(Lead, Lead.qualification_status == "qualified")
    contacted = c(Lead, Lead.last_contacted_at.is_not(None))
    inbound = c(Message, Message.direction == MessageDirection.inbound)
    outbound_sent = c(Message, Message.direction == MessageDirection.outbound,
                      Message.status.in_([MessageStatus.sent, MessageStatus.delivered,
                                          MessageStatus.read]))
    interested = c(Lead, Lead.stage.in_([LeadStage.interested, LeadStage.demo_scheduled,
                                         LeadStage.negotiation, LeadStage.won]))
    demos_booked = _count(db, Appointment, Appointment.workspace_id == wid,
                          Appointment.status.in_([AppointmentStatus.confirmed,
                                                  AppointmentStatus.completed,
                                                  AppointmentStatus.no_show]),
                          Appointment.confirmed_at >= p, Appointment.confirmed_at < q)
    demos_done = _count(db, Appointment, Appointment.workspace_id == wid,
                        Appointment.status == AppointmentStatus.completed,
                        Appointment.completed_at >= p, Appointment.completed_at < q)
    won = c(Lead, Lead.stage == LeadStage.won)
    approvals_pending = _count(db, Approval, Approval.workspace_id == wid,
                               Approval.status == ApprovalStatus.pending)
    agent_failures = _count(db, AgentRun, AgentRun.workspace_id == wid,
                            AgentRun.status == "failed",
                            AgentRun.created_at >= p, AgentRun.created_at < q)
    open_issues = _count(db, SalesIssue, SalesIssue.workspace_id == wid,
                         SalesIssue.status.in_([IssueStatus.open,
                                                IssueStatus.acknowledged]))
    return {
        "period": {"start": p.isoformat(), "end": q.isoformat()},
        "new_leads": new_leads,
        "qualified_leads": qualified,
        "leads_contacted": contacted,
        "outbound_sent": outbound_sent,
        "inbound_replies": inbound,
        "interested_prospects": interested,
        "demos_booked": demos_booked,
        "demos_completed": demos_done,
        "conversions": won,
        "pending_approvals": approvals_pending,
        "agent_failures": agent_failures,
        "open_issues": open_issues,
    }


def generate_report(db: Session, wid: uuid.UUID, report_type: ReportType,
                    period_start: datetime, period_end: datetime,
                    generated_by: str = "system") -> ManagementReport:
    """Idempotent: one report per workspace/type/period."""
    existing = db.scalar(select(ManagementReport).where(
        ManagementReport.workspace_id == wid,
        ManagementReport.report_type == report_type,
        ManagementReport.period_start == period_start))
    if existing:
        return existing
    payload = _report_payload(db, wid, period_start, period_end)
    # weekly extras: funnel + previous-period comparison
    if report_type == ReportType.weekly:
        funnel_data = manager_metrics.funnel(db, wid, days=7)
        prev_start = period_start - timedelta(days=7)
        prev = _report_payload(db, wid, prev_start, period_start)
        payload["funnel"] = funnel_data["stages"]
        payload["previous_period"] = prev
        payload["comparison"] = {
            k: {"current": payload.get(k), "previous": prev.get(k)}
            for k in ("new_leads", "qualified_leads", "outbound_sent",
                      "inbound_replies", "demos_booked", "conversions")
        }
    report = ManagementReport(
        workspace_id=wid, report_type=report_type,
        period_start=period_start, period_end=period_end,
        payload=payload, generated_by=generated_by)
    db.add(report)
    db.flush()
    notify_workspace(db, wid, NotificationType.system,
                     f"{report_type.value.title()} report ready",
                     body=f"{period_start.date()} → {period_end.date()}",
                     link="/manager")
    return report


def ensure_scheduled_reports(db: Session, wid: uuid.UUID) -> list[ManagementReport]:
    """Create today's daily report and this week's weekly report if missing."""
    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)
    # week starts Monday
    week_start = today_start - timedelta(days=now.weekday())
    week_end = week_start + timedelta(days=7)
    out = []
    for rt, ps, pe, key in [
        (ReportType.daily, today_start, tomorrow, f"daily:{today_start.date()}"),
        (ReportType.weekly, week_start, min(week_end, tomorrow),
         f"weekly:{week_start.date()}"),
    ]:
        run = db.scalar(select(AnalysisRun).where(
            AnalysisRun.workspace_id == wid, AnalysisRun.kind == key.split(":")[0] + "_report",
            AnalysisRun.period_key == key))
        if run and run.status == AnalysisRunStatus.succeeded:
            continue
        report = generate_report(db, wid, rt, ps, pe)
        if run is None:
            run = AnalysisRun(workspace_id=wid, kind=rt.value + "_report",
                              period_key=key, status=AnalysisRunStatus.running,
                              started_at=_now())
            db.add(run)
        run.status = AnalysisRunStatus.succeeded
        run.finished_at = _now()
        run.stats = {"report_id": str(report.id)}
        out.append(report)
    db.flush()
    return out


# ---------------------------------------------------------------- intelligence (LLM)


def generate_insights(db: Session, wid: uuid.UUID) -> dict:
    """AI analysis grounded in real metrics. Falls back to heuristic text."""
    perf = manager_metrics.agent_performance(db, wid)
    fun = manager_metrics.funnel(db, wid)
    issues = db.scalars(select(SalesIssue).where(
        SalesIssue.workspace_id == wid,
        SalesIssue.status.in_([IssueStatus.open, IssueStatus.acknowledged]))
        .order_by(SalesIssue.severity.desc()).limit(10)).all()
    recs = db.scalars(select(Recommendation).where(
        Recommendation.workspace_id == wid,
        Recommendation.status == RecommendationStatus.new)
        .order_by(Recommendation.created_at.desc()).limit(10)).all()

    evidence = {
        "agent_metrics": {k: v["metrics"] for k, v in perf.items()},
        "funnel": fun["stages"],
        "open_issues": [{"type": i.issue_type, "severity": i.severity.value,
                         "title": i.title} for i in issues],
        "recommendations": [{"title": r.title, "priority": r.priority.value}
                            for r in recs],
    }

    llm = _get_llm()
    if llm is not None:
        try:
            from pydantic import BaseModel, Field
            class Insight(BaseModel):
                observations: list[str] = Field(default_factory=list)
                hypotheses: list[str] = Field(default_factory=list)
                recommendations: list[str] = Field(default_factory=list)
                data_gaps: list[str] = Field(default_factory=list)
            user = (
                "You are the Sales Manager of an AI sales workforce. Analyze "
                "the following REAL aggregated metrics. Cite only the numbers "
                "provided. Distinguish observations from hypotheses. If data is "
                "insufficient, say so in data_gaps. Never invent metrics.\n\n"
                f"METRICS:\n{_trim(evidence)}")
            out = llm.generate_json(
                "You are a sales operations analyst. Be concise, evidence-based, "
                "and honest about uncertainty.", user, Insight)
            return {"provider": llm.name, "observations": out.observations,
                    "hypotheses": out.hypotheses,
                    "recommendations": out.recommendations,
                    "data_gaps": out.data_gaps, "evidence": evidence}
        except Exception:
            pass

    # heuristic fallback — deterministic, honest
    obs, hyp, rec = [], [], []
    stages = {s["stage"]: s["count"] for s in fun["stages"]}
    if stages.get("discovered", 0) >= 20:
        qrate = stages.get("qualified", 0) / max(stages["discovered"], 1)
        obs.append(f"Qualification rate is {qrate:.0%} "
                   f"({stages.get('qualified',0)}/{stages.get('discovered',0)} leads).")
        if qrate < 0.15:
            hyp.append("Research targeting may be too broad; qualification "
                       "criteria or source quality may need review.")
            rec.append("Review lead-researcher targeting and scoring rules.")
    if stages.get("prospect_replied", 0) and stages.get("prospect_interested", 0):
        irate = stages["prospect_interested"] / stages["prospect_replied"]
        obs.append(f"{irate:.0%} of replying prospects become interested.")
    if issues:
        obs.append(f"{len(issues)} open issues detected, top severity: "
                   f"{issues[0].severity.value}.")
        rec.append(f"Prioritize: {issues[0].title}")
    if not stages.get("discovered"):
        obs.append("No leads yet — the funnel is empty; start lead research.")
        rec.append("Run an initial Lead Research job.")
    gaps = [] if stages.get("discovered") else ["No lead data yet — all "
                                              "downstream metrics unavailable."]
    return {"provider": "heuristic",
            "observations": obs or ["Insufficient data for observations."],
            "hypotheses": hyp, "recommendations": rec,
            "data_gaps": gaps, "evidence": evidence}


def _get_llm():
    try:
        from app.integrations.llm.base import get_llm
        return get_llm()
    except Exception:
        return None


def _trim(evidence: dict, limit: int = 6000) -> str:
    import json
    s = json.dumps(evidence, default=str)
    return s[:limit] + ("…" if len(s) > limit else "")


# ---------------------------------------------------------------- orchestration


def run_analysis(db: Session, wid: uuid.UUID, kind: str = "scan") -> dict:
    """One idempotent analysis pass: issues -> recommendations -> reports."""
    today = _now().date().isoformat()
    run = db.scalar(select(AnalysisRun).where(
        AnalysisRun.workspace_id == wid, AnalysisRun.kind == kind,
        AnalysisRun.period_key == today))
    if run and run.status == AnalysisRunStatus.succeeded:
        return {"skipped": True, "reason": "already ran today"}
    if run is None:
        run = AnalysisRun(workspace_id=wid, kind=kind, period_key=today,
                          status=AnalysisRunStatus.running, started_at=_now())
        db.add(run)
        db.flush()
    try:
        issues = detect_bottlenecks(db, wid)
        recs = generate_recommendations(db, wid)
        reports = ensure_scheduled_reports(db, wid)
        run.status = AnalysisRunStatus.succeeded
        run.finished_at = _now()
        run.stats = {"issues": len(issues), "recommendations": len(recs),
                     "reports": len(reports)}
        db.flush()
        return {"issues": len(issues), "recommendations": len(recs),
                "reports": [str(r.id) for r in reports]}
    except Exception as exc:
        run.status = AnalysisRunStatus.failed
        run.error = str(exc)[:500]
        run.finished_at = _now()
        db.flush()
        raise

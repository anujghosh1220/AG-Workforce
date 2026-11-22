"""Phase 7 — workflow step executors.

Each executor receives (db, workflow, step) and returns a dict stored as
the step output. They reuse existing services/models and never fabricate
results: unconfigured providers produce honest "skipped" outputs.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.models import (
    Agent, AgentRun, Appointment, Campaign, CampaignStatus, Channel,
    Conversation, Lead, AssigneeType, ResearchJob, ResearchJobStatus, Task,
    TaskPriority, TaskStatus,
)
from app.services import context as ctx_svc
from app.services import events as ev
from app.services.helpers import log_activity, notify_workspace
from app.models import NotificationType


def _now():
    return datetime.now(timezone.utc)


def _agent(db, wid, slug):
    return db.scalar(select(Agent).where(Agent.workspace_id == wid,
                                         Agent.slug == slug))


def _run(db, wid, workflow, step, fn):
    """Wrap executor with an AgentRun record + event."""
    from app.models import RunStatus
    from app.services import llm_metering
    agent = _agent(db, wid, step.agent_slug)
    run = None
    if agent:
        run = AgentRun(workspace_id=wid, agent_id=agent.id,
                       status=RunStatus.running,
                       started_at=_now())
        db.add(run)
        db.flush()
    meter_ctx = llm_metering.scope(
        workspace_id=wid, workflow_id=workflow.id, step_id=step.id,
        agent_id=agent.id if agent else None, agent_slug=step.agent_slug)
    try:
        with meter_ctx:
            out = fn(db, workflow, step)
        if run:
            run.status = RunStatus.succeeded
            run.finished_at = _now()
        ev.safe_publish(db, wid, "agent.run_completed",
                        {"agent": step.agent_slug, "step": step.step_key,
                         "workflow_id": str(workflow.id)},
                        workflow_id=workflow.id, step_id=step.id)
        return out
    except Exception as exc:
        if run:
            run.status = RunStatus.failed
            run.error = str(exc)[:500]
            run.finished_at = _now()
        ev.safe_publish(db, wid, "agent.run_failed",
                        {"agent": step.agent_slug, "step": step.step_key,
                         "error": str(exc)[:300], "workflow_id": str(workflow.id)},
                        workflow_id=workflow.id, step_id=step.id)
        raise


# ---------------------------------------------------------------- lead-researcher


def exec_discover_leads(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        # create a real research job; the research worker executes it when
        # a provider is configured. Honest: job may stay queued.
        existing = db.scalar(select(ResearchJob).where(
            ResearchJob.workspace_id == wid,
            ResearchJob.status.in_([ResearchJobStatus.queued,
                                    ResearchJobStatus.running])).limit(1))
        if existing:
            return {"job_id": str(existing.id), "status": "already_queued"}
        cfg = dict(st.input or {})
        job = ResearchJob(workspace_id=wid,
                          config={"city": cfg.get("city"),
                                  "locality": cfg.get("locality"),
                                  "category": cfg.get("category", "cafe"),
                                  "limit": cfg.get("limit", 25)},
                          stats={"discovered": 0, "researched": 0,
                                 "contacts_found": 0, "qualified": 0,
                                 "duplicates": 0, "errors": 0, "imported": 0},
                          created_by_user_id=None)
        db.add(job)
        db.flush()
        ev.safe_publish(db, wid, "lead.research_queued",
                        {"job_id": str(job.id)}, workflow_id=wf.id, step_id=st.id)
        return {"job_id": str(job.id), "status": "queued"}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_research_leads(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        job_id = (st.input or {}).get("job_id") or \
            (wf.context or {}).get("research_job_id")
        if job_id:
            job = db.get(ResearchJob, uuid.UUID(str(job_id)))
            if job and job.workspace_id == wid and job.status in (
                    ResearchJobStatus.completed, ResearchJobStatus.failed):
                stats = job.stats or {}
                return {"job_id": str(job.id), "status": job.status.value,
                        "stats": stats, "error": job.error}
            if job:
                return {"job_id": str(job.id), "status": job.status.value,
                        "note": "research job still in progress"}
        # no job: count current leads as evidence
        n = db.scalar(select(func.count(Lead.id)).where(Lead.workspace_id == wid)) or 0
        return {"status": "no_job", "leads_in_workspace": n,
                "note": "No research job produced; provider may be unconfigured."}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_enrich_lead(db, workflow, step):
    def go(db, wf, st):
        data = ctx_svc.load(db, wf.workspace_id, "lead",
                            lead_id=(st.input or {}).get("lead_id", ""))
        if not data.get("found"):
            return {"found": False}
        return {"found": True, "lead": data}
    return _run(db, workflow.workspace_id, workflow, step, go)


# ---------------------------------------------------------------- sales-executive


def exec_prioritize_prospects(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        leads = ctx_svc.load(db, wid, "leads", limit=100)["leads"]
        top = [l for l in leads if l["stage"] in ("researched", "qualified",
                                                  "contacted", "replied")][:10]
        for l in top:
            tid = f"wf-{wf.id}-prio-{l['id']}"
            exists = db.scalar(select(Task.id).where(
                Task.workspace_id == wid, Task.title == tid))
            if not exists:
                agent = _agent(db, wid, "sales-executive")
                db.add(Task(workspace_id=wid, title=tid,
                            description=f"Review prioritized prospect {l['name']} "
                                        f"(score {l['score']}, stage {l['stage']})",
                            assignee_type=AssigneeType.agent,
                            assignee_agent_id=agent.id if agent else None,
                            priority=TaskPriority.medium,
                            status=TaskStatus.pending,
                            lead_id=uuid.UUID(l["id"])))
        db.flush()
        return {"prioritized": len(top), "leads": top[:5]}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_draft_campaign(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        leads = ctx_svc.load(db, wid, "leads", stage="qualified", limit=50)["leads"]
        name = f"WF-{wf.id.hex[:8]} café outreach"
        existing = db.scalar(select(Campaign).where(
            Campaign.workspace_id == wid, Campaign.name == name))
        if existing:
            return {"campaign_id": str(existing.id), "status": existing.status.value}
        camp = Campaign(workspace_id=wid, name=name, status=CampaignStatus.draft,
                        channel=Channel.email,
                        lead_ids=[l["id"] for l in leads],
                        instructions=(st.input or {}).get("instructions",
                            "Personalized intro for café management software."),
                        stats={"drafted": 0, "sent": 0, "replies": 0})
        db.add(camp)
        db.flush()
        return {"campaign_id": str(camp.id), "status": "draft",
                "lead_count": len(leads),
                "note": "Draft created; requires owner authorization before send."}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_prepare_outreach(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        camp_id = (st.input or {}).get("campaign_id") or \
            (wf.context or {}).get("campaign_id")
        if not camp_id:
            return {"status": "no_campaign"}
        camp = db.get(Campaign, uuid.UUID(str(camp_id)))
        if not camp or camp.workspace_id != wid:
            return {"status": "campaign_not_found"}
        n = len(camp.lead_ids or [])
        return {"campaign_id": str(camp.id), "status": camp.status.value,
                "target_leads": n}
    return _run(db, workflow.workspace_id, workflow, step, go)


# ---------------------------------------------------------------- communication-manager


def exec_handle_replies(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        convs = ctx_svc.load(db, wid, "conversations")["conversations"]
        needs = [c for c in convs if c["needs_human"] or c["state"] == "escalated"]
        open_states = [c for c in convs if c["state"] in
                       ("new", "interested", "scheduling", "demo_scheduled")]
        return {"conversations": len(convs), "needs_human": len(needs),
                "active": len(open_states),
                "interested_ids": [c["id"] for c in convs
                                   if c["state"] in ("interested", "scheduling")]}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_draft_reply(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        conv_id = (st.input or {}).get("conversation_id")
        if not conv_id:
            return {"status": "no_conversation"}
        conv = db.get(Conversation, uuid.UUID(str(conv_id)))
        if not conv or conv.workspace_id != wid:
            return {"status": "not_found"}
        from app.services import outreach
        draft = outreach.generate_reply_draft(db, wid, conv)
        return {"conversation_id": str(conv.id), "draft": draft}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_send_approved_outreach(db, workflow, step):
    """Send only messages whose approval was granted. Provider-honest."""
    def go(db, wf, st):
        wid = wf.workspace_id
        camp_id = (st.input or {}).get("campaign_id") or \
            (wf.context or {}).get("campaign_id")
        if not camp_id:
            return {"status": "no_campaign"}
        camp = db.get(Campaign, uuid.UUID(str(camp_id)))
        if not camp or camp.workspace_id != wid:
            return {"status": "campaign_not_found"}
        if camp.status != CampaignStatus.authorized:
            return {"status": "not_authorized",
                    "note": "Campaign must be authorized by owner first."}
        sent, failed, skipped = 0, 0, 0
        for lid in (camp.lead_ids or [])[: int((st.input or {}).get("limit", 25))]:
            lead = db.get(Lead, uuid.UUID(str(lid)))
            if not lead or lead.workspace_id != wid:
                skipped += 1
                continue
            # reuse existing eligibility gate
            from app.services import outreach
            elig = outreach.eligibility(db, wid, lead, Channel.email)
            if not elig.get("allowed"):
                skipped += 1
                continue
            conv = db.scalar(select(Conversation).where(
                Conversation.workspace_id == wid,
                Conversation.lead_id == lead.id).limit(1))
            if not conv:
                skipped += 1
                continue
            draft = outreach.generate_intro_draft(db, wid, lead,
                                                  camp.instructions or "")
            msg = outreach.create_outbound_draft(
                db, wid, conv, draft.get("subject", ""), draft.get("body", ""),
                agent_slug="communication-manager", approval_required=False)
            res = outreach.send_message(db, wid, msg)
            if res.get("sent") or res.get("status") in ("sent", "queued"):
                sent += 1
            else:
                failed += 1
        camp.stats = {**(camp.stats or {}), "sent": sent, "failed": failed,
                      "skipped": skipped}
        db.flush()
        return {"sent": sent, "failed": failed, "skipped": skipped,
                "note": "Counts reflect provider-confirmed sends only."}
    return _run(db, workflow.workspace_id, workflow, step, go)


# ---------------------------------------------------------------- executive-assistant


def exec_schedule_demos(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        interested = ctx_svc.load(db, wid, "conversations")["conversations"]
        sched = [c for c in interested if c["state"] in ("interested", "scheduling")]
        appts = ctx_svc.load(db, wid, "appointments")["appointments"]
        confirmed = [a for a in appts if a["status"] == "confirmed"]
        # create internal tasks for interested prospects lacking a booking
        booked_leads = {a["lead_id"] for a in appts}
        n_tasks = 0
        for c in sched:
            if c["lead_id"] in booked_leads:
                continue
            agent = _agent(db, wid, "executive-assistant")
            tid = f"wf-{wf.id}-demo-{c['lead_id']}"
            exists = db.scalar(select(Task.id).where(
                Task.workspace_id == wid, Task.title == tid))
            if not exists:
                db.add(Task(workspace_id=wid, title=tid,
                            description="Prospect is interested; offer demo slots.",
                            assignee_type=AssigneeType.agent,
                            assignee_agent_id=agent.id if agent else None,
                            priority=TaskPriority.high,
                            status=TaskStatus.pending,
                            lead_id=uuid.UUID(c["lead_id"])))
                n_tasks += 1
        db.flush()
        return {"interested_conversations": len(sched),
                "confirmed_appointments": len(confirmed),
                "demo_tasks_created": n_tasks}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_manage_appointments(db, workflow, step):
    def go(db, wf, st):
        data = ctx_svc.load(db, wf.workspace_id, "appointments")
        counts = {}
        for a in data["appointments"]:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        return {"total": len(data["appointments"]), "by_status": counts}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_prepare_demo_brief(db, workflow, step):
    def go(db, wf, st):
        appt_id = (st.input or {}).get("appointment_id")
        if not appt_id:
            return {"status": "no_appointment"}
        a = db.get(Appointment, uuid.UUID(str(appt_id)))
        if not a or a.workspace_id != wf.workspace_id:
            return {"status": "not_found"}
        lead = db.get(Lead, a.lead_id)
        brief = {"appointment": a.title, "when": a.start_at.isoformat(),
                 "prospect": lead.business_name if lead else "unknown",
                 "city": lead.city if lead else None,
                 "score": lead.lead_score if lead else None}
        return {"brief": brief}
    return _run(db, workflow.workspace_id, workflow, step, go)


# ---------------------------------------------------------------- sales-manager


def exec_prioritize_pipeline(db, workflow, step):
    def go(db, wf, st):
        from app.services import manager_metrics
        perf = manager_metrics.agent_performance(db, wf.workspace_id)
        return {"agents": {k: v["agent"]["status"] for k, v in perf.items()}}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_track_outcomes(db, workflow, step):
    def go(db, wf, st):
        wid = wf.workspace_id
        fun = __import__("app.services.manager_metrics", fromlist=["x"]).funnel(db, wid)
        stages = {s["stage"]: s["count"] for s in fun["stages"]}
        ev.safe_publish(db, wid, "workflow.outcomes",
                        {"converted": stages.get("customer_converted", 0),
                         "demos_completed": stages.get("demo_completed", 0),
                         "revenue_won": fun["revenue_won"]},
                        workflow_id=wf.id, step_id=st.id)
        return {"funnel": stages, "revenue_won": fun["revenue_won"]}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_generate_report(db, workflow, step):
    def go(db, wf, st):
        from app.services import manager_engine
        now = _now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        rep = manager_engine.generate_report(
            db, wf.workspace_id, __import__("app.models", fromlist=["ReportType"]).ReportType.daily,
            start, start.replace(hour=23, minute=59, second=59),
            generated_by="workflow")
        return {"report_id": str(rep.id)}
    return _run(db, workflow.workspace_id, workflow, step, go)


def exec_detect_bottlenecks(db, workflow, step):
    def go(db, wf, st):
        from app.services import manager_engine
        issues = manager_engine.detect_bottlenecks(db, wf.workspace_id)
        recs = manager_engine.generate_recommendations(db, wf.workspace_id)
        return {"issues": len(issues), "recommendations": len(recs)}
    return _run(db, workflow.workspace_id, workflow, step, go)


EXECUTORS = {
    "exec_discover_leads": exec_discover_leads,
    "exec_research_leads": exec_research_leads,
    "exec_enrich_lead": exec_enrich_lead,
    "exec_prioritize_prospects": exec_prioritize_prospects,
    "exec_draft_campaign": exec_draft_campaign,
    "exec_prepare_outreach": exec_prepare_outreach,
    "exec_handle_replies": exec_handle_replies,
    "exec_draft_reply": exec_draft_reply,
    "exec_send_approved_outreach": exec_send_approved_outreach,
    "exec_schedule_demos": exec_schedule_demos,
    "exec_manage_appointments": exec_manage_appointments,
    "exec_prepare_demo_brief": exec_prepare_demo_brief,
    "exec_prioritize_pipeline": exec_prioritize_pipeline,
    "exec_track_outcomes": exec_track_outcomes,
    "exec_generate_report": exec_generate_report,
    "exec_detect_bottlenecks": exec_detect_bottlenecks,
}

"""Phase 7 — persistent orchestration engine.

- Creates structured plans from objectives (canned workflow templates).
- Executes steps honoring dependencies, parallel groups, retries.
- Approval checkpoints reuse the existing Approval model.
- Execution budgets pause the workflow when exceeded.
- Recovery: queued/running workflows are resumed by the worker after restart.
- Autonomy modes: manual | hybrid | autonomous (per workspace + per agent
  overrides stored in workspace.settings["orchestration"]).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Agent, Approval, ApprovalActionType, ApprovalStatus, RiskLevel,
    Workflow, WorkflowEvent, WorkflowStatus, WorkflowStep, WorkflowStepStatus,
)
from app.services import capabilities, events as ev
from app.services import workflow_steps as steps_mod

logger = logging.getLogger("agworkforce.orchestrator")

DEFAULT_LIMITS = {
    "max_duration_minutes": 240,
    "max_steps": 50,
    "max_retries": 2,
    "max_llm_calls": 100,
    "max_parallel": 4,
    "max_outbound_messages": 100,
    "max_followups": 20,
}

TERMINAL_STEP = {WorkflowStepStatus.completed, WorkflowStepStatus.failed,
                 WorkflowStepStatus.skipped}


# ---------------------------------------------------------------- plans

CAFE_ACQUISITION_PLAN = [
    {"key": "discover", "agent": "lead-researcher", "task": "discover_leads",
     "title": "Discover café leads", "seq": 1},
    {"key": "research", "agent": "lead-researcher", "task": "research_leads",
     "title": "Research & qualify discovered cafés", "seq": 2,
     "depends_on": ["discover"]},
    {"key": "prioritize", "agent": "sales-manager", "task": "prioritize_pipeline",
     "title": "Prioritize qualified prospects", "seq": 3,
     "depends_on": ["research"]},
    {"key": "sales_prioritize", "agent": "sales-executive",
     "task": "prioritize_prospects",
     "title": "Sales Executive prioritizes outreach targets", "seq": 4,
     "depends_on": ["research"], "parallel_group": "prep"},
    {"key": "draft_campaign", "agent": "sales-executive",
     "task": "draft_campaign", "title": "Draft personalized campaign",
     "seq": 5, "depends_on": ["prioritize"], "parallel_group": "prep"},
    {"key": "approve_campaign", "agent": "sales-manager",
     "task": "generate_report", "title": "Prepare campaign for owner approval",
     "seq": 6, "depends_on": ["draft_campaign"]},
    {"key": "await_approval", "agent": "sales-manager",
     "task": "track_outcomes", "title": "Await owner campaign authorization",
     "seq": 7, "depends_on": ["approve_campaign"]},
    {"key": "send_outreach", "agent": "communication-manager",
     "task": "send_approved_outreach",
     "title": "Send authorized outreach", "seq": 8,
     "depends_on": ["await_approval"]},
    {"key": "handle_replies", "agent": "communication-manager",
     "task": "handle_replies", "title": "Manage inbound replies",
     "seq": 9, "depends_on": ["send_outreach"]},
    {"key": "schedule_demos", "agent": "executive-assistant",
     "task": "schedule_demos", "title": "Schedule demos for interested prospects",
     "seq": 10, "depends_on": ["handle_replies"]},
    {"key": "track_outcomes", "agent": "sales-manager",
     "task": "track_outcomes", "title": "Track demo & conversion outcomes",
     "seq": 11, "depends_on": ["schedule_demos"]},
    {"key": "final_report", "agent": "sales-manager", "task": "generate_report",
     "title": "Generate final management report", "seq": 12,
     "depends_on": ["track_outcomes"]},
]

PLANS = {"cafe_acquisition": CAFE_ACQUISITION_PLAN}


def create_workflow(db: Session, wid: uuid.UUID, objective: str,
                    workflow_type: str = "cafe_acquisition",
                    plan_override: list | None = None,
                    created_by: str = "user") -> Workflow:
    plan = plan_override or PLANS.get(workflow_type)
    if plan is None:
        raise ValueError(f"unknown workflow_type '{workflow_type}'")
    wf = Workflow(
        workspace_id=wid, workflow_type=workflow_type, objective=objective,
        plan={"steps": plan}, status=WorkflowStatus.queued,
        limits=dict(DEFAULT_LIMITS),
        autonomy_mode=_autonomy_mode(db, wid),
        correlation_id=uuid.uuid4().hex[:16], created_by=created_by)
    db.add(wf)
    db.flush()
    for spec in plan:
        st = WorkflowStep(
            workspace_id=wid, workflow_id=wf.id,
            step_key=spec["key"], agent_slug=spec["agent"],
            task_type=spec["task"], title=spec.get("title", spec["key"]),
            sequence=spec["seq"], depends_on=spec.get("depends_on", []),
            parallel_group=spec.get("parallel_group"),
            max_attempts=DEFAULT_LIMITS["max_retries"] + 1,
            input=spec.get("input", {}))
        db.add(st)
    db.flush()
    ev.safe_publish(db, wid, "workflow.created",
                    {"workflow_id": str(wf.id), "type": workflow_type,
                     "objective": objective[:200]},
                    workflow_id=wf.id)
    return wf


# ---------------------------------------------------------------- autonomy


def _workspace_settings(db, wid) -> dict:
    from app.models import Workspace
    ws = db.get(Workspace, wid)
    return (ws.settings or {}) if ws else {}


def _autonomy_mode(db, wid) -> str:
    return _workspace_settings(db, wid).get("orchestration", {}).get(
        "mode", "hybrid")


def autonomy_allows(db, wid, agent_slug, task_type, step_risk="low") -> tuple[bool, str]:
    """Return (allowed, reason). Sensitive actions never bypass approval."""
    settings = _workspace_settings(db, wid).get("orchestration", {})
    mode = settings.get("mode", "hybrid")
    agent_cfg = settings.get("agents", {}).get(agent_slug, {})
    eff_mode = agent_cfg.get("mode", mode)
    if task_type in capabilities.NEVER_AUTONOMOUS:
        return False, "task requires explicit approval regardless of mode"
    if capabilities.requires_approval(task_type):
        return False, "task requires approval checkpoint"
    if eff_mode == "manual":
        return False, "workspace/agent in manual mode"
    if eff_mode == "hybrid" and step_risk in ("high", "critical"):
        return False, "hybrid mode requires approval for high-risk steps"
    return True, "autonomous"


# ---------------------------------------------------------------- execution


def _deps_met(step: WorkflowStep, by_key: dict) -> bool:
    for dep in step.depends_on or []:
        d = by_key.get(dep)
        if d is None or d.status not in TERMINAL_STEP or d.status == WorkflowStepStatus.failed:
            return False
    return True


def _budget_exceeded(db, wf: Workflow) -> str | None:
    lim = {**DEFAULT_LIMITS, **(wf.limits or {})}
    now = datetime.now(timezone.utc)
    started = wf.started_at or wf.created_at
    if started and (now - started).total_seconds() > lim["max_duration_minutes"] * 60:
        return f"max_duration_minutes exceeded ({lim['max_duration_minutes']})"
    steps = db.scalars(select(WorkflowStep).where(
        WorkflowStep.workflow_id == wf.id)).all()
    if len(steps) > lim["max_steps"]:
        return f"max_steps exceeded ({lim['max_steps']})"
    llm = sum(1 for s in steps if "llm" in (s.task_type or ""))
    if llm > lim["max_llm_calls"]:
        return "max_llm_calls exceeded"
    return None


def _create_approval(db, wf, step) -> Approval:
    agent = db.scalar(select(Agent).where(Agent.workspace_id == wf.workspace_id,
                                          Agent.slug == step.agent_slug))
    ap = Approval(
        workspace_id=wf.workspace_id,
        action_type=ApprovalActionType.send_campaign
        if step.task_type == "send_approved_outreach"
        else ApprovalActionType.custom_action,
        title=f"Workflow approval: {step.title}",
        payload={"workflow_id": str(wf.id), "step_id": str(step.id),
                 "step_key": step.step_key, "task_type": step.task_type},
        risk_level=RiskLevel.high,
        requested_by_agent_id=agent.id if agent else None,
        status=ApprovalStatus.pending)
    db.add(ap)
    db.flush()
    step.approval_id = ap.id
    return ap


def run_workflow(db: Session, wf: Workflow, max_parallel: int | None = None) -> Workflow:
    """Execute ready steps until blocked, complete, or paused. Synchronous
    engine; parallel groups run sequentially within a pass (documented)."""
    if wf.status in (WorkflowStatus.completed, WorkflowStatus.failed,
                     WorkflowStatus.cancelled):
        return wf
    wf.status = WorkflowStatus.running
    if not wf.started_at:
        wf.started_at = datetime.now(timezone.utc)
    db.flush()

    reason = _budget_exceeded(db, wf)
    if reason:
        wf.status = WorkflowStatus.paused
        wf.error = f"budget exceeded: {reason}"
        ev.safe_publish(db, wf.workspace_id, "workflow.paused",
                        {"workflow_id": str(wf.id), "reason": reason},
                        workflow_id=wf.id)
        return wf

    steps = {s.step_key: s for s in db.scalars(select(WorkflowStep).where(
        WorkflowStep.workflow_id == wf.id).order_by(WorkflowStep.sequence)).all()}
    progressed = True
    while progressed:
        progressed = False
        for step in sorted(steps.values(), key=lambda s: s.sequence):
            if step.status in TERMINAL_STEP or step.status == WorkflowStepStatus.running:
                continue
            if step.status == WorkflowStepStatus.awaiting_approval:
                continue
            if not _deps_met(step, steps):
                continue
            # dependency failed -> skip
            failed_dep = any(
                steps.get(d) and steps[d].status == WorkflowStepStatus.failed
                for d in (step.depends_on or []))
            if failed_dep:
                step.status = WorkflowStepStatus.skipped
                step.error = "dependency failed"
                step.finished_at = datetime.now(timezone.utc)
                progressed = True
                continue

            # capability check — never delegate unsupported actions
            if not capabilities.agent_supported(step.agent_slug, step.task_type):
                step.status = WorkflowStepStatus.failed
                step.error = f"agent '{step.agent_slug}' cannot perform " \
                             f"'{step.task_type}'"
                step.finished_at = datetime.now(timezone.utc)
                progressed = True
                continue

            # approval gate
            if (capabilities.requires_approval(step.task_type)
                    or not autonomy_allows(db, wf.workspace_id,
                                           step.agent_slug, step.task_type)[0]):
                if step.status != WorkflowStepStatus.awaiting_approval:
                    if step.task_type in capabilities.NEVER_AUTONOMOUS or \
                       capabilities.requires_approval(step.task_type):
                        _create_approval(db, wf, step)
                        step.status = WorkflowStepStatus.awaiting_approval
                        wf.status = WorkflowStatus.waiting_approval
                        ev.safe_publish(db, wf.workspace_id, "workflow.awaiting_approval",
                                        {"workflow_id": str(wf.id),
                                         "step": step.step_key},
                                        workflow_id=wf.id, step_id=step.id)
                        db.flush()
                        return wf

            step.status = WorkflowStepStatus.running
            step.attempts += 1
            step.started_at = datetime.now(timezone.utc)
            db.flush()
            try:
                exec_name = capabilities.executor_for(step.agent_slug,
                                                      step.task_type)
                out = steps_mod.EXECUTORS[exec_name](db, wf, step)
                step.output = out or {}
                step.status = WorkflowStepStatus.completed
                step.finished_at = datetime.now(timezone.utc)
                step.error = None
                progressed = True
            except Exception as exc:
                step.error = str(exc)[:500]
                if step.attempts < step.max_attempts:
                    step.status = WorkflowStepStatus.ready  # retry next pass
                else:
                    step.status = WorkflowStepStatus.failed
                    step.finished_at = datetime.now(timezone.utc)
                progressed = True
                ev.safe_publish(db, wf.workspace_id, "workflow.step_failed",
                                {"workflow_id": str(wf.id), "step": step.step_key,
                                 "attempt": step.attempts, "error": str(exc)[:300]},
                                workflow_id=wf.id, step_id=step.id)

    # all steps terminal?
    statuses = {s.status for s in steps.values()}
    if statuses <= TERMINAL_STEP:
        if WorkflowStepStatus.failed in statuses:
            wf.status = WorkflowStatus.failed
            wf.error = next((s.error for s in steps.values()
                             if s.status == WorkflowStepStatus.failed), "step failed")
            ev.safe_publish(db, wf.workspace_id, "workflow.failed",
                            {"workflow_id": str(wf.id), "error": wf.error},
                            workflow_id=wf.id)
        elif statuses and all(s.status in (WorkflowStepStatus.completed,
                                           WorkflowStepStatus.skipped)
                              for s in steps.values()):
            wf.status = WorkflowStatus.completed
            wf.finished_at = datetime.now(timezone.utc)
            ev.safe_publish(db, wf.workspace_id, "workflow.completed",
                            {"workflow_id": str(wf.id)},
                            workflow_id=wf.id)
    db.flush()
    return wf


def step_for_approval(db, wf: Workflow, step: WorkflowStep) -> bool:
    """Called when a step's approval is decided. Returns True if approved."""
    if not step.approval_id:
        return False
    ap = db.get(Approval, step.approval_id)
    if ap is None:
        return False
    # invalidate if the underlying action materially changed:
    # approval payload must still match the step identity
    payload = ap.payload or {}
    if payload.get("step_id") != str(step.id):
        return False
    return ap.status == ApprovalStatus.approved


def resume_after_approval(db: Session, wf: Workflow) -> Workflow:
    """Re-check awaiting steps after approval decisions; continue workflow."""
    for step in wf.steps:
        if step.status == WorkflowStepStatus.awaiting_approval:
            if step_for_approval(db, wf, step):
                step.status = WorkflowStepStatus.ready
                wf.status = WorkflowStatus.running
            elif step.approval_id:
                ap = db.get(Approval, step.approval_id)
                if ap and ap.status in (ApprovalStatus.rejected,
                                        ApprovalStatus.expired):
                    step.status = WorkflowStepStatus.failed
                    step.error = "approval rejected"
                    step.finished_at = datetime.now(timezone.utc)
                    wf.status = WorkflowStatus.failed
                    wf.error = "approval rejected"
    db.flush()
    return run_workflow(db, wf)


# ---------------------------------------------------------------- controls


def pause_workflow(db, wf: Workflow, reason: str = "manual") -> Workflow:
    if wf.status in (WorkflowStatus.running, WorkflowStatus.queued):
        wf.status = WorkflowStatus.paused
        wf.paused_at = datetime.now(timezone.utc)
        ev.safe_publish(db, wf.workspace_id, "workflow.paused",
                        {"workflow_id": str(wf.id), "reason": reason},
                        workflow_id=wf.id)
    db.flush()
    return wf


def resume_workflow(db, wf: Workflow) -> Workflow:
    if wf.status == WorkflowStatus.paused:
        wf.status = WorkflowStatus.running
        wf.paused_at = None
        ev.safe_publish(db, wf.workspace_id, "workflow.resumed",
                        {"workflow_id": str(wf.id)}, workflow_id=wf.id)
        db.flush()
        return run_workflow(db, wf)
    return wf


def cancel_workflow(db, wf: Workflow, reason: str = "manual") -> Workflow:
    if wf.status not in (WorkflowStatus.completed, WorkflowStatus.failed,
                         WorkflowStatus.cancelled):
        wf.status = WorkflowStatus.cancelled
        wf.finished_at = datetime.now(timezone.utc)
        wf.error = reason
        for s in wf.steps:
            if s.status not in TERMINAL_STEP:
                s.status = WorkflowStepStatus.skipped
                s.error = "workflow cancelled"
                s.finished_at = datetime.now(timezone.utc)
        ev.safe_publish(db, wf.workspace_id, "workflow.cancelled",
                        {"workflow_id": str(wf.id), "reason": reason},
                        workflow_id=wf.id)
    db.flush()
    return wf


def recover_interrupted(db: Session) -> int:
    """On startup: resume workflows left running/queued by a restart."""
    rows = db.scalars(select(Workflow).where(
        Workflow.status.in_([WorkflowStatus.running,
                             WorkflowStatus.queued,
                             WorkflowStatus.waiting_approval]))).all()
    n = 0
    for wf in rows:
        try:
            if wf.status == WorkflowStatus.waiting_approval:
                resume_after_approval(db, wf)
            else:
                run_workflow(db, wf)
            n += 1
        except Exception:
            logger.exception("recovery failed for workflow %s", wf.id)
    return n

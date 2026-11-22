"""Phase 7 — orchestration API: workflows, steps, events, autonomy, budgets."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    ActorType, Approval, ApprovalActionType, ApprovalStatus, UserRole,
    Workflow, WorkflowEvent, WorkflowStatus, WorkflowStep,
)
from app.schemas import ORMModel
from app.services import capabilities, events as ev, orchestrator
from app.services.helpers import get_or_404, log_activity

router = APIRouter(prefix="/workflows", tags=["workflows"])
WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)
ADMIN_ROLES = (UserRole.owner, UserRole.admin)


class WorkflowCreate(BaseModel):
    objective: str = Field(min_length=3, max_length=2000)
    workflow_type: str = "cafe_acquisition"


class WorkflowUpdate(BaseModel):
    limits: dict | None = None
    autonomy_mode: str | None = None


class WorkflowOut(ORMModel):
    id: uuid.UUID
    workflow_type: str
    objective: str
    status: str
    autonomy_mode: str
    correlation_id: str
    current_step: int
    error: str | None
    limits: dict
    created_by: str
    started_at: datetime | None
    paused_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    step_count: int = 0
    completed_steps: int = 0


class StepOut(ORMModel):
    id: uuid.UUID
    step_key: str
    agent_slug: str
    task_type: str
    title: str
    sequence: int
    depends_on: list
    status: str
    input: dict
    output: dict
    attempts: int
    max_attempts: int
    error: str | None
    approval_id: uuid.UUID | None
    started_at: datetime | None
    finished_at: datetime | None


class EventOut(ORMModel):
    id: uuid.UUID
    event_type: str
    payload: dict
    status: str
    attempts: int
    workflow_id: uuid.UUID | None
    step_id: uuid.UUID | None
    created_at: datetime
    processed_at: datetime | None


def _wf_out(db, wf: Workflow) -> dict:
    steps = list(wf.steps)
    return WorkflowOut.model_validate(wf).model_dump() | {
        "step_count": len(steps),
        "completed_steps": sum(1 for s in steps
                               if s.status.value in ("completed", "skipped"))}


# ---------------------------------------------------------------- workflows


@router.get("", response_model=Page[WorkflowOut])
def list_workflows(status_: str | None = Query(None, alias="status"),
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   auth: AuthContext = Depends(get_current_auth),
                   db: Session = Depends(get_db)):
    q = select(Workflow).where(Workflow.workspace_id == auth.workspace.id)
    if status_:
        q = q.where(Workflow.status == status_)
    q = q.order_by(Workflow.created_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([_wf_out(db, w) for w in items], total, page, page_size)


@router.post("", response_model=WorkflowOut, status_code=status.HTTP_201_CREATED)
def create(body: WorkflowCreate,
           auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
           db: Session = Depends(get_db)):
    try:
        wf = orchestrator.create_workflow(
            db, auth.workspace.id, body.objective, body.workflow_type,
            created_by=auth.user.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


@router.get("/plans")
def plans(auth: AuthContext = Depends(get_current_auth)):
    return {"plans": {k: [{"key": s["key"], "agent": s["agent"],
                           "task": s["task"], "title": s.get("title", ""),
                           "depends_on": s.get("depends_on", [])}
                          for s in v]
                      for k, v in orchestrator.PLANS.items()},
            "capabilities": capabilities.registry()}


@router.get("/{workflow_id}", response_model=WorkflowOut)
def get_workflow(workflow_id: uuid.UUID,
                 auth: AuthContext = Depends(get_current_auth),
                 db: Session = Depends(get_db)):
    return _wf_out(db, get_or_404(db, Workflow, workflow_id,
                                  auth.workspace.id))


@router.get("/{workflow_id}/steps", response_model=list[StepOut])
def workflow_steps(workflow_id: uuid.UUID,
                   auth: AuthContext = Depends(get_current_auth),
                   db: Session = Depends(get_db)):
    wf = get_or_404(db, Workflow, workflow_id, auth.workspace.id)
    return [StepOut.model_validate(s) for s in
            sorted(wf.steps, key=lambda s: s.sequence)]


@router.post("/{workflow_id}/run", response_model=WorkflowOut)
def run(workflow_id: uuid.UUID,
        auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
        db: Session = Depends(get_db)):
    wf = get_or_404(db, Workflow, workflow_id, auth.workspace.id)
    if wf.status == WorkflowStatus.cancelled:
        raise HTTPException(409, "workflow cancelled")
    wf = orchestrator.run_workflow(db, wf)
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


@router.post("/{workflow_id}/pause", response_model=WorkflowOut)
def pause(workflow_id: uuid.UUID,
          auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
          db: Session = Depends(get_db)):
    wf = orchestrator.pause_workflow(
        db, get_or_404(db, Workflow, workflow_id, auth.workspace.id))
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


@router.post("/{workflow_id}/resume", response_model=WorkflowOut)
def resume(workflow_id: uuid.UUID,
           auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
           db: Session = Depends(get_db)):
    wf = orchestrator.resume_workflow(
        db, get_or_404(db, Workflow, workflow_id, auth.workspace.id))
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


@router.post("/{workflow_id}/cancel", response_model=WorkflowOut)
def cancel(workflow_id: uuid.UUID,
           auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
           db: Session = Depends(get_db)):
    wf = orchestrator.cancel_workflow(
        db, get_or_404(db, Workflow, workflow_id, auth.workspace.id))
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


@router.patch("/{workflow_id}", response_model=WorkflowOut)
def update_workflow(workflow_id: uuid.UUID, body: WorkflowUpdate,
                    auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                    db: Session = Depends(get_db)):
    wf = get_or_404(db, Workflow, workflow_id, auth.workspace.id)
    if body.limits:
        allowed = set(orchestrator.DEFAULT_LIMITS)
        wf.limits = {**(wf.limits or {}),
                     **{k: v for k, v in body.limits.items() if k in allowed}}
    if body.autonomy_mode in ("manual", "hybrid", "autonomous"):
        wf.autonomy_mode = body.autonomy_mode
    db.commit()
    db.refresh(wf)
    return _wf_out(db, wf)


# ---------------------------------------------------------------- steps & events


@router.get("/{workflow_id}/events", response_model=list[EventOut])
def workflow_events(workflow_id: uuid.UUID,
                    auth: AuthContext = Depends(get_current_auth),
                    db: Session = Depends(get_db)):
    get_or_404(db, Workflow, workflow_id, auth.workspace.id)
    rows = ev.list_events(db, auth.workspace.id, limit=200,
                          workflow_id=workflow_id)
    return [EventOut.model_validate(e) for e in rows]


@router.get("/events/all", response_model=Page[EventOut])
def all_events(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
               auth: AuthContext = Depends(get_current_auth),
               db: Session = Depends(get_db)):
    q = select(WorkflowEvent).where(WorkflowEvent.workspace_id == auth.workspace.id)
    q = q.order_by(WorkflowEvent.created_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([EventOut.model_validate(e) for e in items],
                      total, page, page_size)


# ---------------------------------------------------------------- autonomy config


class OrchestrationConfig(BaseModel):
    mode: str = Field(default="hybrid", pattern="^(manual|hybrid|autonomous)$")
    agents: dict = Field(default_factory=dict)


@router.get("/config/orchestration")
def get_orchestration_config(auth: AuthContext = Depends(get_current_auth),
                             db: Session = Depends(get_db)):
    from app.models import Workspace
    ws = db.get(Workspace, auth.workspace.id)
    cfg = (ws.settings or {}).get("orchestration",
                                  {"mode": "hybrid", "agents": {}})
    return cfg


@router.put("/config/orchestration")
def put_orchestration_config(body: OrchestrationConfig,
                             auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                             db: Session = Depends(get_db)):
    from app.models import Workspace
    ws = db.get(Workspace, auth.workspace.id)
    settings = dict(ws.settings or {})
    settings["orchestration"] = body.model_dump()
    ws.settings = settings
    db.commit()
    return settings["orchestration"]


# ---------------------------------------------------------------- approvals bridge

@router.post("/approvals/{approval_id}/decide")
def decide_approval(approval_id: uuid.UUID, approved: bool,
                    auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                    db: Session = Depends(get_db)):
    """Owner decides a workflow approval; resumes the workflow if approved."""
    ap = get_or_404(db, Approval, approval_id, auth.workspace.id)
    if ap.status != ApprovalStatus.pending:
        raise HTTPException(409, "approval already decided")
    ap.status = ApprovalStatus.approved if approved else ApprovalStatus.rejected
    ap.reviewed_by_user_id = auth.user.id
    ap.decided_at = datetime.now(timezone.utc)
    wid = auth.workspace.id
    log_activity(db, wid, "approval.decided", actor_id=auth.user.id,
                 actor_type=ActorType.user,
                 description=f"Workflow approval {'approved' if approved else 'rejected'}",
                 metadata={"approval_id": str(ap.id)})
    result = {"approval_id": str(ap.id), "status": ap.status.value}
    wf_id = (ap.payload or {}).get("workflow_id")
    if wf_id:
        wf = db.get(Workflow, uuid.UUID(wf_id))
        if wf and wf.workspace_id == wid:
            wf = orchestrator.resume_after_approval(db, wf)
            result["workflow_status"] = wf.status.value
    db.commit()
    return result

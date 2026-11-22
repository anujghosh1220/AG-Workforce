"""Sales Manager API — command centre endpoints (Phase 6)."""
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
    ActorType, Agent, AssigneeType, IssueStatus, Lead, ManagementReport,
    Recommendation, RecommendationStatus, ReportType, SalesIssue, Task,
    TaskPriority, TaskStatus, UserRole,
)
from app.schemas import ORMModel
from app.services import manager_engine, manager_metrics
from app.services.helpers import get_or_404, log_activity, notify_workspace
from app.models import NotificationType

router = APIRouter(prefix="/manager", tags=["manager"])
WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)
ADMIN_ROLES = (UserRole.owner, UserRole.admin)


# ---- schemas (local, phase-scoped) ----
class IssueOut(ORMModel):
    id: uuid.UUID
    issue_type: str
    severity: str
    title: str
    detail: str
    agent_slug: str | None
    workflow: str | None
    evidence: dict
    suggested_resolution: str
    status: str
    first_detected_at: datetime
    last_detected_at: datetime
    resolved_at: datetime | None


class RecommendationOut(ORMModel):
    id: uuid.UUID
    title: str
    explanation: str
    supporting_data: dict
    expected_benefit: str
    priority: str
    suggested_action: str
    task_spec: dict
    related_records: dict
    confidence: str
    source: str
    status: str
    issue_id: uuid.UUID | None
    created_at: datetime
    decided_at: datetime | None


class ReportOut(ORMModel):
    id: uuid.UUID
    report_type: str
    period_start: datetime
    period_end: datetime
    payload: dict
    generated_at: datetime
    generated_by: str


class RecStatusIn(BaseModel):
    status: RecommendationStatus


class ReportGenerateIn(BaseModel):
    report_type: ReportType = ReportType.daily


# ---- endpoints ----

@router.get("/overview")
def overview(auth: AuthContext = Depends(get_current_auth),
             db: Session = Depends(get_db)):
    wid = auth.workspace.id
    from sqlalchemy import func
    perf = manager_metrics.agent_performance(db, wid)
    fun = manager_metrics.funnel(db, wid)
    stages = {s["stage"]: s["count"] for s in fun["stages"]}
    open_issues = db.scalar(select(func.count(SalesIssue.id)).where(
        SalesIssue.workspace_id == wid,
        SalesIssue.status.in_([IssueStatus.open, "acknowledged"]))) or 0
    upcoming = db.scalar(select(func.count(__import__('app.models', fromlist=['Appointment']).Appointment.id)).where(
        __import__('app.models', fromlist=['Appointment']).Appointment.workspace_id == wid,
        __import__('app.models', fromlist=['Appointment']).Appointment.status == "confirmed",
        __import__('app.models', fromlist=['Appointment']).Appointment.start_at >= datetime.now(timezone.utc))) or 0
    return {
        "total_leads": stages.get("discovered", 0),
        "qualified_prospects": stages.get("qualified", 0),
        "interested_prospects": stages.get("prospect_interested", 0),
        "upcoming_demos": upcoming,
        "completed_demos": stages.get("demo_completed", 0),
        "converted_customers": stages.get("customer_converted", 0),
        "revenue_won": fun["revenue_won"],
        "open_issues": open_issues,
        "agents": {k: v["agent"] for k, v in perf.items()},
    }


@router.get("/agents/performance")
def agent_performance(auth: AuthContext = Depends(get_current_auth),
                     db: Session = Depends(get_db)):
    return manager_metrics.agent_performance(db, auth.workspace.id)


@router.get("/funnel")
def funnel(days: int = Query(90, ge=1, le=365),
           auth: AuthContext = Depends(get_current_auth),
           db: Session = Depends(get_db)):
    return manager_metrics.funnel(db, auth.workspace.id, days=days)


@router.get("/intelligence")
def intelligence(auth: AuthContext = Depends(get_current_auth),
                db: Session = Depends(get_db)):
    return manager_engine.generate_insights(db, auth.workspace.id)


@router.post("/intelligence/refresh")
def refresh_intelligence(auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                         db: Session = Depends(get_db)):
    return manager_engine.generate_insights(db, auth.workspace.id)


@router.post("/analyze")
def run_analysis(auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                 db: Session = Depends(get_db)):
    result = manager_engine.run_analysis(db, auth.workspace.id, kind="scan")
    db.commit()
    return result


@router.get("/issues", response_model=Page[IssueOut])
def list_issues(status_: str | None = Query(None, alias="status"),
                page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                auth: AuthContext = Depends(get_current_auth),
                db: Session = Depends(get_db)):
    q = select(SalesIssue).where(SalesIssue.workspace_id == auth.workspace.id)
    if status_:
        q = q.where(SalesIssue.status == status_)
    q = q.order_by(SalesIssue.last_detected_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([IssueOut.model_validate(i) for i in items], total, page, page_size)


@router.post("/issues/{issue_id}/{action}")
def issue_action(issue_id: uuid.UUID, action: str,
                 auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                 db: Session = Depends(get_db)):
    wid = auth.workspace.id
    issue = get_or_404(db, SalesIssue, issue_id, wid)
    if action == "acknowledge":
        issue.status = IssueStatus.acknowledged
    elif action == "resolve":
        issue.status = IssueStatus.resolved
        issue.resolved_at = datetime.now(timezone.utc)
    elif action == "dismiss":
        issue.status = IssueStatus.dismissed
        issue.resolved_at = datetime.now(timezone.utc)
    else:
        raise HTTPException(400, "invalid action")
    log_activity(db, wid, f"issue.{action}", actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Issue '{issue.title}' {action}")
    db.commit()
    return IssueOut.model_validate(issue)


@router.get("/recommendations", response_model=Page[RecommendationOut])
def list_recommendations(status_: str | None = Query(None, alias="status"),
                         page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
                         auth: AuthContext = Depends(get_current_auth),
                         db: Session = Depends(get_db)):
    q = select(Recommendation).where(Recommendation.workspace_id == auth.workspace.id)
    if status_:
        q = q.where(Recommendation.status == status_)
    q = q.order_by(Recommendation.created_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([RecommendationOut.model_validate(r) for r in items], total, page, page_size)


@router.post("/recommendations/{rec_id}/status")
def rec_status(rec_id: uuid.UUID, body: RecStatusIn,
               auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
               db: Session = Depends(get_db)):
    wid = auth.workspace.id
    rec = get_or_404(db, Recommendation, rec_id, wid)
    rec.status = body.status
    if body.status in (RecommendationStatus.accepted, RecommendationStatus.dismissed,
                       RecommendationStatus.completed):
        rec.decided_at = datetime.now(timezone.utc)
    db.commit()
    return RecommendationOut.model_validate(rec)


@router.post("/recommendations/{rec_id}/accept")
def accept_recommendation(rec_id: uuid.UUID,
                          auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                          db: Session = Depends(get_db)):
    """Accept a recommendation: creates its task_spec as a real Task."""
    wid = auth.workspace.id
    rec = get_or_404(db, Recommendation, rec_id, wid)
    spec = rec.task_spec or {}
    slug = spec.get("agent_slug", "sales-manager")
    agent = db.scalar(select(Agent).where(Agent.workspace_id == wid, Agent.slug == slug))
    task = None
    if agent and spec.get("title"):
        # idempotent: skip if identical open task exists
        exists = db.scalar(select(Task.id).where(
            Task.workspace_id == wid, Task.title == spec["title"],
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress])))
        if not exists:
            task = Task(
                workspace_id=wid, title=spec["title"][:300],
                description=spec.get("description", rec.explanation)[:4000],
                assignee_type=AssigneeType.agent, assignee_agent_id=agent.id,
                priority=rec.priority, status=TaskStatus.pending,
                lead_id=(rec.related_records or {}).get("lead_id"))
            db.add(task)
            db.flush()
    rec.status = RecommendationStatus.accepted
    rec.decided_at = datetime.now(timezone.utc)
    log_activity(db, wid, "recommendation.accepted", actor_id=auth.user.id,
                 actor_type=ActorType.user,
                 description=f"Recommendation accepted: {rec.title}",
                 metadata={"recommendation_id": str(rec.id),
                           "task_id": str(task.id) if task else None})
    db.commit()
    return {"recommendation": RecommendationOut.model_validate(rec),
            "task_id": str(task.id) if task else None}


@router.get("/reports", response_model=Page[ReportOut])
def list_reports(report_type: ReportType | None = None,
                 page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                 auth: AuthContext = Depends(get_current_auth),
                 db: Session = Depends(get_db)):
    q = select(ManagementReport).where(ManagementReport.workspace_id == auth.workspace.id)
    if report_type:
        q = q.where(ManagementReport.report_type == report_type)
    q = q.order_by(ManagementReport.generated_at.desc())
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([ReportOut.model_validate(r) for r in items], total, page, page_size)


@router.post("/reports/generate", response_model=ReportOut,
             status_code=status.HTTP_201_CREATED)
def generate_report(body: ReportGenerateIn,
                    auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                    db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    if body.report_type == ReportType.daily:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(hour=23, minute=59, second=59)
    else:
        start = (now - __import__('datetime').timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0)
        end = start + __import__('datetime').timedelta(days=7)
    report = manager_engine.generate_report(
        db, auth.workspace.id, body.report_type, start, end,
        generated_by=auth.user.email)
    db.commit()
    db.refresh(report)
    return ReportOut.model_validate(report)


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: uuid.UUID,
               auth: AuthContext = Depends(get_current_auth),
               db: Session = Depends(get_db)):
    return ReportOut.model_validate(get_or_404(db, ManagementReport, report_id,
                                               auth.workspace.id))

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import Agent, AgentRun, ActorType, Task, TaskStatus, UserRole
from app.schemas import AgentCreate, AgentOut, AgentRunOut, AgentUpdate
from app.services.helpers import get_or_404, log_activity

router = APIRouter(prefix="/agents", tags=["agents"])


def _enrich(agent: Agent, db: Session) -> dict:
    data = AgentOut.model_validate(agent).model_dump()
    open_tasks = db.scalar(
        select(func.count(Task.id)).where(
            Task.workspace_id == agent.workspace_id,
            Task.assignee_agent_id == agent.id,
            Task.status.in_([TaskStatus.pending, TaskStatus.in_progress, TaskStatus.blocked]),
        )
    ) or 0
    data["current_workload"] = open_tasks
    last_run = db.scalar(
        select(AgentRun)
        .where(AgentRun.agent_id == agent.id)
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    if last_run:
        data["last_activity_at"] = last_run.created_at
        data["tasks_completed"] = db.scalar(
            select(func.count(AgentRun.id)).where(
                AgentRun.agent_id == agent.id, AgentRun.status == "succeeded"
            )
        ) or 0
    return data


@router.get("", response_model=Page[AgentOut])
def list_agents(
    page: int = 1,
    page_size: int = 50,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    q = select(Agent).where(Agent.workspace_id == wid).order_by(Agent.name)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    agents = db.scalars(q.offset((page - 1) * page_size).limit(page_size)).all()
    items = [AgentOut.model_validate(a) for a in agents]
    return Page.build(items, total, page, page_size)


@router.post("", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
def create_agent(
    body: AgentCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    if db.scalar(select(Agent).where(Agent.workspace_id == wid, Agent.slug == body.slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Agent slug already exists")
    agent = Agent(workspace_id=wid, **body.model_dump())
    db.add(agent)
    db.flush()
    log_activity(db, wid, "agent.created", actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Agent '{agent.name}' created")
    db.commit()
    return AgentOut.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentOut)
def get_agent(agent_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    agent = get_or_404(db, Agent, agent_id, auth.workspace.id)
    data = _enrich(agent, db)
    return AgentOut(**data)


@router.patch("/{agent_id}", response_model=AgentOut)
def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
    db: Session = Depends(get_db),
):
    agent = get_or_404(db, Agent, agent_id, auth.workspace.id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    db.flush()
    log_activity(db, auth.workspace.id, "agent.updated", actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Agent '{agent.name}' updated",
                 metadata={"agent_id": str(agent.id)})
    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.get("/{agent_id}/policy")
def agent_policy(agent_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    """Effective permission policy: catalog + this agent's approval requirements."""
    from app.services.permissions import catalog_public, requires_approval

    agent = get_or_404(db, Agent, agent_id, auth.workspace.id)
    catalog = catalog_public()
    for entry in catalog:
        entry["requires_approval_for_agent"] = requires_approval(agent, entry["action"])
    return {
        "agent_id": str(agent.id),
        "agent_slug": agent.slug,
        "enabled": agent.enabled,
        "permissions": agent.permissions,
        "actions": catalog,
    }


@router.get("/{agent_id}/runs", response_model=Page[AgentRunOut])
def list_agent_runs(
    agent_id: uuid.UUID,
    page: int = 1,
    page_size: int = 25,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    get_or_404(db, Agent, agent_id, auth.workspace.id)
    q = (
        select(AgentRun)
        .where(AgentRun.workspace_id == auth.workspace.id, AgentRun.agent_id == agent_id)
        .order_by(AgentRun.created_at.desc())
    )
    items, total, page, page_size = paginate(db, q, page, page_size)
    return Page.build([AgentRunOut.model_validate(r) for r in items], total, page, page_size)

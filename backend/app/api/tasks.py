import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import Agent, Lead, Task, TaskStatus, User, UserRole
from app.schemas import TaskCreate, TaskOut, TaskUpdate
from app.services.helpers import get_or_404, log_activity
from app.models import ActorType

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _decorate(task: Task, db: Session) -> dict:
    data = TaskOut.model_validate(task).model_dump()
    if task.assignee_agent_id:
        agent = db.get(Agent, task.assignee_agent_id)
        data["assignee_name"] = agent.name if agent else None
    elif task.assignee_user_id:
        user = db.get(User, task.assignee_user_id)
        data["assignee_name"] = user.full_name or user.email if user else None
    else:
        data["assignee_name"] = None
    if task.lead_id:
        lead = db.get(Lead, task.lead_id)
        data["lead_business_name"] = lead.business_name if lead else None
    return data


@router.get("", response_model=Page[TaskOut])
def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status_: TaskStatus | None = Query(None, alias="status"),
    assignee_agent_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Task).where(Task.workspace_id == wid)
    if status_:
        query = query.where(Task.status == status_)
    if assignee_agent_id:
        query = query.where(Task.assignee_agent_id == assignee_agent_id)
    if lead_id:
        query = query.where(Task.lead_id == lead_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Task.title.ilike(like), Task.description.ilike(like)))
    query = query.order_by(Task.created_at.desc())

    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([TaskOut(**_decorate(t, db)) for t in items], total, page, page_size)


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
def create_task(
    body: TaskCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    data = body.model_dump()
    if data["assignee_type"] == "agent" and data.get("assignee_agent_id"):
        get_or_404(db, Agent, data["assignee_agent_id"], wid)
    if data.get("lead_id"):
        get_or_404(db, Lead, data["lead_id"], wid)
    task = Task(workspace_id=wid, created_by_user_id=auth.user.id, **data)
    db.add(task)
    db.flush()
    if task.assignee_agent_id:
        agent = db.get(Agent, task.assignee_agent_id)
        if agent:
            agent.current_workload = (agent.current_workload or 0) + 1
    log_activity(db, wid, "task.created", lead_id=task.lead_id, actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Task '{task.title}' created")
    db.commit()
    db.refresh(task)
    return TaskOut(**_decorate(task, db))


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: uuid.UUID,
    body: TaskUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    task = get_or_404(db, Task, task_id, wid)
    data = body.model_dump(exclude_unset=True)

    old_status = task.status
    for field, value in data.items():
        setattr(task, field, value)

    # keep agent workload roughly in sync when a task completes
    if task.status == TaskStatus.completed and old_status != TaskStatus.completed and task.assignee_agent_id:
        agent = db.get(Agent, task.assignee_agent_id)
        if agent and agent.current_workload > 0:
            agent.current_workload -= 1
        agent.tasks_completed = (agent.tasks_completed or 0) + 1 if agent else 0

    log_activity(db, wid, "task.updated", lead_id=task.lead_id, actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Task '{task.title}' updated",
                 metadata={"fields": list(data.keys())})
    db.commit()
    db.refresh(task)
    return TaskOut(**_decorate(task, db))

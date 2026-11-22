import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    ActorType,
    Agent,
    Approval,
    ApprovalActionType,
    ApprovalStatus,
    Lead,
    NotificationType,
    RiskLevel,
    User,
    UserRole,
)
from app.schemas import ApprovalCreate, ApprovalOut, ApprovalReview
from app.services.helpers import get_or_404, log_activity, notify_workspace

router = APIRouter(prefix="/approvals", tags=["approvals"])

REVIEWABLE = {ApprovalStatus.approved, ApprovalStatus.rejected, ApprovalStatus.cancelled}


def _decorate(approval: Approval, db: Session) -> dict:
    data = ApprovalOut.model_validate(approval).model_dump()
    if approval.lead_id:
        lead = db.get(Lead, approval.lead_id)
        data["lead_business_name"] = lead.business_name if lead else None
    requester = None
    if approval.requested_by_agent_id:
        agent = db.get(Agent, approval.requested_by_agent_id)
        requester = agent.name if agent else None
    elif approval.requested_by_user_id:
        user = db.get(User, approval.requested_by_user_id)
        requester = (user.full_name or user.email) if user else None
    data["requester_name"] = requester
    return data


@router.get("", response_model=Page[ApprovalOut])
def list_approvals(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    status_: ApprovalStatus | None = Query(None, alias="status"),
    action_type: str | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Approval).where(Approval.workspace_id == wid)
    if status_:
        query = query.where(Approval.status == status_)
    if action_type:
        query = query.where(Approval.action_type == action_type)
    if q:
        query = query.where(Approval.title.ilike(f"%{q.strip()}%"))
    query = query.order_by(Approval.created_at.desc())

    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([ApprovalOut(**_decorate(a, db)) for a in items], total, page, page_size)


@router.post("", response_model=ApprovalOut, status_code=status.HTTP_201_CREATED)
def create_approval(
    body: ApprovalCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    data = body.model_dump()
    if data.get("lead_id"):
        get_or_404(db, Lead, data["lead_id"], wid)
    if data.get("requested_by_agent_id"):
        get_or_404(db, Agent, data["requested_by_agent_id"], wid)
    data["requested_by_user_id"] = auth.user.id
    data.pop("risk_level", None)
    from app.services.permissions import spec_for
    spec = spec_for(data["action_type"].value)
    approval = Approval(workspace_id=wid, risk_level=spec.risk if spec else RiskLevel.high, **data)
    db.add(approval)
    db.flush()
    log_activity(db, wid, "approval.requested", lead_id=approval.lead_id,
                 actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Approval requested: {approval.title}",
                 metadata={"approval_id": str(approval.id), "action_type": approval.action_type.value})
    notify_workspace(db, wid, NotificationType.pending_approval,
                     f"Approval requested: {approval.title}", link="/approvals")
    db.commit()
    db.refresh(approval)
    return ApprovalOut(**_decorate(approval, db))


@router.patch("/{approval_id}", response_model=ApprovalOut)
def review_approval(
    approval_id: uuid.UUID,
    body: ApprovalReview,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    approval = get_or_404(db, Approval, approval_id, wid)
    if approval.status != ApprovalStatus.pending:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_409_CONFLICT, "Approval already decided")
    if body.status not in REVIEWABLE and body.status != ApprovalStatus.expired:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid review status")

    approval.status = body.status
    approval.reviewed_by_user_id = auth.user.id
    approval.decided_at = datetime.now(timezone.utc)

    log_activity(db, wid, f"approval.{body.status.value}", lead_id=approval.lead_id,
                 actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Approval '{approval.title}' {body.status.value}",
                 metadata={"approval_id": str(approval.id), "note": body.note})

    # NOTE: approving a record never executes the external action.
    # Exception (Phase 5): schedule_meeting approvals created by the Executive
    # Assistant carry an appointment_id — approving them confirms the booking.
    if body.status == ApprovalStatus.approved and approval.action_type == ApprovalActionType.schedule_meeting:
        appt_id = (approval.payload or {}).get("appointment_id")
        if appt_id:
            try:
                from app.models import Appointment
                from app.services import appointments as appt_svc
                appt = db.get(Appointment, uuid.UUID(appt_id))
                if appt and appt.workspace_id == wid and appt.status.value in (
                        "proposed", "awaiting_confirmation", "rescheduling_requested"):
                    appt_svc.confirm_appointment(db, wid, appt,
                                                 actor_type=ActorType.user,
                                                 actor_id=auth.user.id)
                    log_activity(db, wid, "appointment.confirmed_via_approval",
                                 lead_id=appt.lead_id, actor_id=auth.user.id,
                                 actor_type=ActorType.user,
                                 description=f"Appointment '{appt.title}' confirmed via approval")
            except Exception:
                import logging
                logging.getLogger("agworkforce.approvals").exception(
                    "failed to confirm appointment from approval")

    db.commit()
    db.refresh(approval)
    return ApprovalOut(**_decorate(approval, db))


@router.get("/pending/count")
def pending_count(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    count = db.scalar(
        select(func.count(Approval.id)).where(
            Approval.workspace_id == auth.workspace.id, Approval.status == ApprovalStatus.pending
        )
    )
    return {"count": count or 0}

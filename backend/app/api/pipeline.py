import uuid

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.core.database import get_db
from app.models import (
    ActorType,
    Lead,
    LeadStage,
    LeadStageHistory,
    NotificationType,
    Opportunity,
    OpportunityStatus,
    UserRole,
)
from app.schemas import LeadOut, PipelineStageOut, StageUpdate
from app.services.helpers import get_or_404, log_activity, notify_workspace

router = APIRouter(tags=["pipeline"])

TERMINAL_STAGES = {LeadStage.won, LeadStage.lost, LeadStage.do_not_contact}


@router.get("/pipeline", response_model=list[PipelineStageOut])
def get_pipeline(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    wid = auth.workspace.id
    rows = db.execute(
        select(Lead.stage, func.count(Lead.id), func.coalesce(func.sum(Lead.estimated_value), 0))
        .where(Lead.workspace_id == wid)
        .group_by(Lead.stage)
    ).all()
    by_stage = {stage: (count, float(value)) for stage, count, value in rows}
    return [
        PipelineStageOut(
            stage=stage,
            count=by_stage.get(stage, (0, 0.0))[0],
            total_value=by_stage.get(stage, (0, 0.0))[1],
        )
        for stage in LeadStage
    ]


@router.get("/leads/{lead_id}/stage-history")
def stage_history(lead_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth),
                  db: Session = Depends(get_db)):
    lead = get_or_404(db, Lead, lead_id, auth.workspace.id)
    rows = db.scalars(select(LeadStageHistory).where(
        LeadStageHistory.lead_id == lead.id).order_by(
        LeadStageHistory.created_at)).all()
    return [{"from_stage": h.from_stage, "to_stage": h.to_stage,
             "actor": h.actor, "source": h.source, "reason": h.reason,
             "at": h.created_at.isoformat()} for h in rows]


@router.patch("/leads/{lead_id}/stage", response_model=LeadOut)
def update_stage(
    lead_id: uuid.UUID,
    body: StageUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    lead = get_or_404(db, Lead, lead_id, auth.workspace.id)
    old_stage = lead.stage
    if old_stage == body.stage:
        return LeadOut.model_validate(lead)

    lead.stage = body.stage
    note = body.note or f"Stage changed from {old_stage.value} to {body.stage.value}"

    log_activity(
        db, auth.workspace.id, "lead.stage_changed", lead_id=lead.id,
        actor_id=auth.user.id, actor_type=ActorType.user,
        description=f"'{lead.business_name}': {old_stage.value} → {body.stage.value}",
        metadata={"from": old_stage.value, "to": body.stage.value, "note": note},
    )

    wid = auth.workspace.id
    if body.stage == LeadStage.won:
        notify_workspace(db, wid, NotificationType.won_deal,
                         f"Deal won: {lead.business_name}", link=f"/leads/{lead.id}")
        opp = db.scalar(
            select(Opportunity).where(
                Opportunity.workspace_id == wid,
                Opportunity.lead_id == lead.id,
                Opportunity.status == OpportunityStatus.open,
            ).limit(1)
        )
        if opp:
            opp.status = OpportunityStatus.won
            opp.stage = LeadStage.won
    elif body.stage == LeadStage.demo_scheduled:
        notify_workspace(db, wid, NotificationType.demo_request,
                         f"Demo scheduled: {lead.business_name}", link=f"/leads/{lead.id}")
    elif body.stage == LeadStage.interested:
        notify_workspace(db, wid, NotificationType.interested_prospect,
                         f"Interested prospect: {lead.business_name}", link=f"/leads/{lead.id}")

    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


class BulkStageUpdate(BaseModel):
    lead_ids: list[uuid.UUID]
    stage: LeadStage


@router.post("/pipeline/bulk-stage", status_code=status.HTTP_200_OK)
def bulk_stage_update(
    body: BulkStageUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    updated = 0
    for lead_id in body.lead_ids:
        lead = db.scalar(select(Lead).where(Lead.id == lead_id, Lead.workspace_id == wid))
        if lead is None or lead.stage == body.stage:
            continue
        old = lead.stage
        lead.stage = body.stage
        log_activity(db, wid, "lead.stage_changed", lead_id=lead.id, actor_id=auth.user.id,
                     actor_type=ActorType.user,
                     description=f"'{lead.business_name}': {old.value} → {body.stage.value} (bulk)",
                     metadata={"from": old.value, "to": body.stage.value})
        updated += 1
    db.commit()
    return {"updated": updated}

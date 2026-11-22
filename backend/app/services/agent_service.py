"""Agent execution foundation.

Phase 1.5 pipeline: permission classification -> compliance gate -> knowledge
load -> run record -> approval gate (when required).

This service performs NO external side effects. An approval record is never
treated as an executed action.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Agent,
    AgentRun,
    Approval,
    ApprovalActionType,
    ApprovalStatus,
    Channel,
    KnowledgeCategory,
    ProductKnowledge,
    RiskLevel,
    RunStatus,
)
from app.services import compliance
from app.services.helpers import log_activity, notify_workspace
from app.services.permissions import requires_approval, spec_for
from app.models import ActorType, NotificationType

# Approval action types that map to outbound channels for compliance checks.
CHANNEL_FOR_ACTION = {
    "send_email": Channel.email,
    "send_whatsapp": Channel.whatsapp,
    "send_approved_followup": Channel.email,
    "send_whatsapp_business": Channel.whatsapp,
    "initiate_first_contact": Channel.email,
}

APPROVAL_TYPE_FOR_ACTION = {
    "send_email": ApprovalActionType.send_email,
    "send_whatsapp": ApprovalActionType.send_whatsapp,
    "schedule_meeting": ApprovalActionType.schedule_meeting,
    "commit_meeting_terms": ApprovalActionType.schedule_meeting,
    "offer_discount": ApprovalActionType.offer_discount,
    "change_pricing": ApprovalActionType.change_pricing,
    "send_approved_followup": ApprovalActionType.send_email,
    "send_whatsapp_business": ApprovalActionType.send_whatsapp,
    "initiate_first_contact": ApprovalActionType.send_email,
    "schedule_reminder": ApprovalActionType.schedule_meeting,
    "update_prospect_status": ApprovalActionType.send_email,
    "contractual_promise": ApprovalActionType.offer_discount,
    "check_availability": ApprovalActionType.schedule_meeting,
    "propose_appointment_slots": ApprovalActionType.schedule_meeting,
    "create_booking_proposal": ApprovalActionType.schedule_meeting,
    "generate_meeting_brief": ApprovalActionType.schedule_meeting,
    "confirm_appointment": ApprovalActionType.schedule_meeting,
    "reschedule_appointment": ApprovalActionType.schedule_meeting,
    "cancel_appointment": ApprovalActionType.schedule_meeting,
    "send_booking_confirmation": ApprovalActionType.send_email,
    "create_calendar_event": ApprovalActionType.schedule_meeting,
    "commit_appointment_terms": ApprovalActionType.schedule_meeting,
}


def load_workspace_knowledge(db: Session, workspace_id: uuid.UUID, category: KnowledgeCategory | None = None) -> list[ProductKnowledge]:
    query = select(ProductKnowledge).where(ProductKnowledge.workspace_id == workspace_id)
    if category:
        query = query.where(ProductKnowledge.category == category)
    return list(db.scalars(query.order_by(ProductKnowledge.sort_order)).all())


def execute_task(
    db: Session,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
    task_description: str,
    *,
    requested_action: str | None = None,
    action_payload: dict | None = None,
    lead_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> AgentRun:
    """Run the structural agent pipeline for a task.

    1. Load agent, verify enabled.
    2. Classify the requested action's risk and approval requirement.
    3. For outbound-channel actions, enforce consent/suppression checks.
    4. Record an AgentRun (input, knowledge loaded, token fields).
    5. Create an Approval record when required — never execute the action.
    """
    agent = db.scalar(select(Agent).where(Agent.id == agent_id, Agent.workspace_id == workspace_id))
    if agent is None:
        raise ValueError("Agent not found")
    if not agent.enabled:
        raise PermissionError("Agent is disabled")

    spec = spec_for(requested_action) if requested_action else None
    needs_approval = requires_approval(agent, requested_action) if requested_action else False
    risk = spec.risk if spec else RiskLevel.medium

    run = AgentRun(
        workspace_id=workspace_id,
        agent_id=agent.id,
        task_id=task_id,
        status=RunStatus.running,
        input_payload={
            "description": task_description,
            "action": requested_action,
            "risk_level": risk.value,
            "requires_approval": needs_approval,
        },
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    # ---- compliance gate for outbound actions (agents cannot bypass)
    channel = CHANNEL_FOR_ACTION.get(requested_action or "")
    if channel is not None:
        payload = action_payload or {}
        contact_value = payload.get("to") or payload.get("phone") or payload.get("email")
        reason = compliance.outbound_block_reason(
            db, workspace_id, channel, value=contact_value, lead_id=lead_id
        )
        if reason:
            run.status = RunStatus.failed
            run.error = f"Compliance block: {reason}"
            run.output_payload["compliance_block"] = reason
            run.finished_at = datetime.now(timezone.utc)
            agent.status = "error"
            agent.last_activity_at = datetime.now(timezone.utc)
            log_activity(
                db, workspace_id, "agent.compliance_block",
                lead_id=lead_id, actor_type=ActorType.agent, actor_id=agent.id,
                description=f"Agent '{agent.name}' blocked from {requested_action}: {reason}",
            )
            db.flush()
            return run

    knowledge = load_workspace_knowledge(db, workspace_id)
    run.output_payload["knowledge_entries_loaded"] = len(knowledge)

    if requested_action and needs_approval:
        approval = Approval(
            workspace_id=workspace_id,
            action_type=APPROVAL_TYPE_FOR_ACTION.get(requested_action, ApprovalActionType.send_email),
            risk_level=risk,
            status=ApprovalStatus.pending,
            title=f"{agent.name}: {requested_action}",
            payload=action_payload or {},
            requested_by_agent_id=agent.id,
            lead_id=lead_id,
        )
        db.add(approval)
        db.flush()
        run.status = RunStatus.awaiting_approval
        run.output_payload["approval_id"] = str(approval.id)
        notify_workspace(
            db, workspace_id, NotificationType.pending_approval,
            f"Approval required ({risk.value} risk): {approval.title}", link="/approvals",
        )
        log_activity(
            db, workspace_id, "agent.awaiting_approval",
            lead_id=lead_id, actor_type=ActorType.agent, actor_id=agent.id,
            description=f"Agent '{agent.name}' requested approval for {requested_action} ({risk.value} risk)",
        )
    else:
        # Phase 1: no LLM call and no external action is performed.
        run.status = RunStatus.succeeded
        run.output_payload["note"] = "Phase 1: execution recorded; no external action performed."
        agent.tasks_completed = (agent.tasks_completed or 0) + 1
        log_activity(
            db, workspace_id, "agent.run_completed",
            lead_id=lead_id, actor_type=ActorType.agent, actor_id=agent.id,
            description=f"Agent '{agent.name}' completed run for: {task_description[:120]}",
        )

    agent.last_activity_at = datetime.now(timezone.utc)
    run.finished_at = datetime.now(timezone.utc)
    db.flush()
    return run

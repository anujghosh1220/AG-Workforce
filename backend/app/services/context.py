"""Phase 7 — structured context loader for workflow steps.

Returns only the slices a task needs; never dumps the whole workspace.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Agent, Appointment, AppointmentStatus, Campaign, Conversation, Lead,
    LeadStage, Message, MessageDirection, Opportunity, ProductKnowledge,
    ResearchResult, Task, Workflow,
)


def load(db: Session, wid: uuid.UUID, kind: str, **filters) -> dict:
    if kind == "lead":
        lead = db.get(Lead, uuid.UUID(str(filters["lead_id"])))
        if not lead or lead.workspace_id != wid:
            return {"found": False}
        return {"found": True, "id": str(lead.id), "business_name": lead.business_name,
                "city": lead.city, "stage": lead.stage.value,
                "score": lead.lead_score, "email": lead.email, "phone": lead.phone,
                "website": lead.website}
    if kind == "leads":
        stage = filters.get("stage")
        limit = int(filters.get("limit", 50))
        q = select(Lead).where(Lead.workspace_id == wid)
        if stage:
            q = q.where(Lead.stage == stage)
        q = q.order_by(Lead.lead_score.desc().nullslast()).limit(limit)
        return {"leads": [{"id": str(l.id), "name": l.business_name,
                           "city": l.city, "stage": l.stage.value,
                           "score": l.lead_score} for l in db.scalars(q).all()]}
    if kind == "research_evidence":
        rid = filters.get("result_id")
        if rid:
            r = db.get(ResearchResult, uuid.UUID(str(rid)))
            if not r or r.workspace_id != wid:
                return {"found": False}
            return {"found": True, "business_name": r.business_name,
                    "evidence": {k: getattr(r, k) for k in (
                        "website", "phone", "email", "address", "city",
                        "business_category", "is_operating")}}
        rows = db.scalars(select(ResearchResult).where(
            ResearchResult.workspace_id == wid).limit(50)).all()
        return {"results": [{"id": str(r.id), "name": r.business_name,
                             "status": r.status.value} for r in rows]}
    if kind == "knowledge":
        rows = db.scalars(select(ProductKnowledge).where(
            ProductKnowledge.workspace_id == wid).limit(50)).all()
        return {"entries": [{"title": k.title, "category": k.category.value}
                            for k in rows]}
    if kind == "conversations":
        convs = db.scalars(select(Conversation).where(
            Conversation.workspace_id == wid).limit(50)).all()
        out = []
        for c in convs:
            last = db.scalar(select(Message).where(
                Message.conversation_id == c.id)
                .order_by(Message.created_at.desc()).limit(1))
            out.append({"id": str(c.id), "lead_id": str(c.lead_id),
                        "state": c.state, "needs_human": c.needs_human,
                        "last_message": (last.body[:200] if last else None)})
        return {"conversations": out}
    if kind == "appointments":
        rows = db.scalars(select(Appointment).where(
            Appointment.workspace_id == wid,
            Appointment.status.in_([AppointmentStatus.confirmed,
                                    AppointmentStatus.completed,
                                    AppointmentStatus.no_show,
                                    AppointmentStatus.proposed])).limit(50)).all()
        return {"appointments": [{"id": str(a.id), "title": a.title,
                                  "start_at": a.start_at.isoformat(),
                                  "status": a.status.value,
                                  "lead_id": str(a.lead_id)} for a in rows]}
    if kind == "campaigns":
        rows = db.scalars(select(Campaign).where(
            Campaign.workspace_id == wid).limit(50)).all()
        return {"campaigns": [{"id": str(c.id), "name": c.name,
                               "status": c.status.value,
                               "stats": c.stats} for c in rows]}
    if kind == "tasks":
        rows = db.scalars(select(Task).where(Task.workspace_id == wid).limit(50)).all()
        return {"tasks": [{"id": str(t.id), "title": t.title,
                           "status": t.status.value,
                           "assignee_agent_id": str(t.assignee_agent_id)
                           if t.assignee_agent_id else None} for t in rows]}
    if kind == "workflow_history":
        wfs = db.scalars(select(Workflow).where(
            Workflow.workspace_id == wid).order_by(
            Workflow.created_at.desc()).limit(10)).all()
        return {"workflows": [{"id": str(w.id), "type": w.workflow_type,
                               "status": w.status.value,
                               "objective": w.objective[:120]} for w in wfs]}
    return {"error": f"unknown context kind '{kind}'"}


def agent_brief(db: Session, wid: uuid.UUID, slug: str) -> dict:
    """Minimal standing context for an agent about itself."""
    agent = db.scalar(select(Agent).where(Agent.workspace_id == wid,
                                          Agent.slug == slug))
    return {"slug": slug, "name": agent.name if agent else slug,
            "status": agent.status.value if agent else "unknown",
            "enabled": agent.enabled if agent else False}

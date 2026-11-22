import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    ActorType,
    Contact,
    Lead,
    LeadStage,
    LeadSource,
    NotificationType,
    QualificationStatus,
    UserRole,
)
from app.schemas import (
    ContactCreate,
    ContactOut,
    ContactUpdate,
    LeadCreate,
    LeadOut,
    LeadSourceCreate,
    LeadSourceOut,
    LeadUpdate,
)
from app.services.helpers import get_or_404, log_activity, notify_workspace

router = APIRouter(tags=["crm"])

LEAD_SORTABLE = {
    "created_at": Lead.created_at,
    "updated_at": Lead.updated_at,
    "business_name": Lead.business_name,
    "lead_score": Lead.lead_score,
    "estimated_value": Lead.estimated_value,
    "last_contacted_at": Lead.last_contacted_at,
}


# ---------------------------------------------------------- lead sources


@router.get("/lead-sources", response_model=list[LeadSourceOut])
def list_sources(auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    return db.scalars(
        select(LeadSource).where(LeadSource.workspace_id == auth.workspace.id).order_by(LeadSource.name)
    ).all()


@router.post("/lead-sources", response_model=LeadSourceOut, status_code=status.HTTP_201_CREATED)
def create_source(
    body: LeadSourceCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    src = LeadSource(workspace_id=auth.workspace.id, **body.model_dump())
    db.add(src)
    db.commit()
    db.refresh(src)
    return src


# ---------------------------------------------------------- leads


@router.get("/leads", response_model=Page[LeadOut])
def list_leads(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    q: str | None = None,
    stage: LeadStage | None = None,
    qualification_status: QualificationStatus | None = None,
    source: str | None = None,
    city: str | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    sort: str = "created_at",
    order: str = "desc",
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Lead).where(Lead.workspace_id == wid)

    if q:
        like = f"%{q.strip()}%"
        query = query.where(
            or_(
                Lead.business_name.ilike(like),
                Lead.contact_person.ilike(like),
                Lead.email.ilike(like),
                Lead.phone.ilike(like),
                Lead.city.ilike(like),
            )
        )
    if stage:
        query = query.where(Lead.stage == stage)
    if qualification_status:
        query = query.where(Lead.qualification_status == qualification_status)
    if source:
        query = query.where(Lead.source == source)
    if city:
        query = query.where(Lead.city.ilike(f"%{city}%"))
    if min_score is not None:
        query = query.where(Lead.lead_score >= min_score)
    if max_score is not None:
        query = query.where(Lead.lead_score <= max_score)

    column = LEAD_SORTABLE.get(sort, Lead.created_at)
    query = query.order_by(desc(column) if order == "desc" else asc(column))

    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([LeadOut.model_validate(l) for l in items], total, page, page_size)


@router.post("/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    body: LeadCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    if body.source_id:
        get_or_404(db, LeadSource, body.source_id, wid)
    lead = Lead(workspace_id=wid, **body.model_dump())
    db.add(lead)
    db.flush()
    log_activity(db, wid, "lead.created", lead_id=lead.id, actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Lead '{lead.business_name}' created")
    if lead.qualification_status == QualificationStatus.qualified:
        notify_workspace(db, wid, NotificationType.new_qualified_lead,
                         f"New qualified lead: {lead.business_name}",
                         body=lead.contact_person or "", link=f"/leads/{lead.id}")
    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: uuid.UUID, auth: AuthContext = Depends(get_current_auth), db: Session = Depends(get_db)):
    lead = get_or_404(db, Lead, lead_id, auth.workspace.id)
    return LeadOut.model_validate(lead)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: uuid.UUID,
    body: LeadUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    lead = get_or_404(db, Lead, lead_id, auth.workspace.id)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(lead, field, value)
    db.flush()
    log_activity(db, auth.workspace.id, "lead.updated", lead_id=lead.id, actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Lead '{lead.business_name}' updated",
                 metadata={"fields": list(data.keys())})
    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.delete("/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_lead(
    lead_id: uuid.UUID,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
    db: Session = Depends(get_db),
):
    lead = get_or_404(db, Lead, lead_id, auth.workspace.id)
    name = lead.business_name
    db.delete(lead)
    log_activity(db, auth.workspace.id, "lead.deleted", actor_id=auth.user.id,
                 actor_type=ActorType.user, description=f"Lead '{name}' deleted",
                 metadata={"lead_id": str(lead_id)})
    db.commit()


# ---------------------------------------------------------- contacts


@router.get("/contacts", response_model=Page[ContactOut])
def list_contacts(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    lead_id: uuid.UUID | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Contact).where(Contact.workspace_id == wid)
    if lead_id:
        lead = get_or_404(db, Lead, lead_id, wid)
        query = query.where(Contact.lead_id == lead.id)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Contact.name.ilike(like), Contact.email.ilike(like)))
    query = query.order_by(Contact.name)
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([ContactOut.model_validate(c) for c in items], total, page, page_size)


@router.post("/contacts", response_model=ContactOut, status_code=status.HTTP_201_CREATED)
def create_contact(
    body: ContactCreate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    data = body.model_dump()
    if data.get("lead_id"):
        get_or_404(db, Lead, data["lead_id"], wid)
    contact = Contact(workspace_id=wid, **data)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return ContactOut.model_validate(contact)


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdate,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin, UserRole.member)),
    db: Session = Depends(get_db),
):
    contact = get_or_404(db, Contact, contact_id, auth.workspace.id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    db.commit()
    db.refresh(contact)
    return ContactOut.model_validate(contact)

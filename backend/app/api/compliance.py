import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.models import (
    Channel,
    ChannelConsent,
    ConsentStatus,
    Lead,
    SuppressionRecord,
    UserRole,
)
from app.schemas import (
    ComplianceCheckOut,
    ConsentCreate,
    ConsentOut,
    SuppressionCreate,
    SuppressionOut,
)
from app.services import compliance
from app.services.helpers import get_or_404

router = APIRouter(prefix="/compliance", tags=["compliance"])
WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)


# ---------------------------------------------------------- consents


@router.get("/consents", response_model=Page[ConsentOut])
def list_consents(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    channel: Channel | None = None,
    status_: ConsentStatus | None = Query(None, alias="status"),
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(ChannelConsent).where(ChannelConsent.workspace_id == wid)
    if channel:
        query = query.where(ChannelConsent.channel == channel)
    if status_:
        query = query.where(ChannelConsent.status == status_)
    if q:
        query = query.where(ChannelConsent.value.ilike(f"%{q.strip()}%"))
    query = query.order_by(ChannelConsent.created_at.desc())
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([ConsentOut.model_validate(c) for c in items], total, page, page_size)


@router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
def upsert_consent(
    body: ConsentCreate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    if body.lead_id:
        get_or_404(db, Lead, body.lead_id, wid)
    row = compliance.record_consent(
        db, wid, body.channel, body.value, body.status,
        source=body.source, lead_id=body.lead_id,
    )
    db.commit()
    db.refresh(row)
    return ConsentOut.model_validate(row)


# ---------------------------------------------------------- suppressions


@router.get("/suppressions", response_model=Page[SuppressionOut])
def list_suppressions(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    channel: Channel | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(SuppressionRecord).where(SuppressionRecord.workspace_id == wid)
    if channel:
        query = query.where(SuppressionRecord.channel == channel)
    if q:
        query = query.where(SuppressionRecord.value.ilike(f"%{q.strip()}%"))
    query = query.order_by(SuppressionRecord.created_at.desc())
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([SuppressionOut.model_validate(s) for s in items], total, page, page_size)


@router.post("/suppressions", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def add_suppression(
    body: SuppressionCreate,
    auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    record = SuppressionRecord(
        workspace_id=wid,
        channel=body.channel,
        value=body.value.strip().lower(),
        reason=body.reason,
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_409_CONFLICT, "Value already suppressed for this channel")
    db.refresh(record)
    return SuppressionOut.model_validate(record)


@router.delete("/suppressions/{suppression_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_suppression(
    suppression_id: uuid.UUID,
    auth: AuthContext = Depends(require_roles(UserRole.owner, UserRole.admin)),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    record = db.scalar(
        select(SuppressionRecord).where(
            SuppressionRecord.id == suppression_id, SuppressionRecord.workspace_id == wid
        )
    )
    if record is None:
        from fastapi import HTTPException
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Suppression record not found")
    db.delete(record)
    db.commit()


# ---------------------------------------------------------- check


@router.get("/check", response_model=ComplianceCheckOut)
def check_contact(
    channel: Channel,
    value: str | None = None,
    lead_id: uuid.UUID | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    if lead_id:
        get_or_404(db, Lead, lead_id, wid)
    reason = compliance.outbound_block_reason(
        db, wid, channel, value=value, lead_id=lead_id
    )
    consent = compliance.consent_status(db, wid, channel, value) if value else None
    suppressed = compliance.is_suppressed(db, wid, channel, value) if value else False
    return ComplianceCheckOut(
        allowed=reason is None,
        reason=reason,
        consent_status=consent.value if consent else None,
        suppressed=suppressed,
    )

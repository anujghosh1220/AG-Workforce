"""Appointments, availability & scheduling settings API (Phase 5)."""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, get_current_auth, require_roles
from app.api.pagination import Page, paginate
from app.core.database import get_db
from app.integrations.calendar.registry import available_providers
from app.models import (
    Agent,
    Appointment,
    AppointmentReminder,
    AppointmentStatus,
    AppointmentType,
    Lead,
    SchedulingSettings,
    UserRole,
)
from app.schemas import (
    AppointmentBookSlot,
    AppointmentCancel,
    AppointmentCreate,
    AppointmentOut,
    AppointmentPropose,
    AppointmentReminderOut,
    AppointmentReschedule,
    AppointmentUpdate,
    AssistantDashboard,
    SchedulingSettingsOut,
    SchedulingSettingsUpdate,
    SlotOut,
)
from app.services import appointments as appt_svc
from app.services import availability
from app.services.helpers import get_or_404, log_activity
from app.models import ActorType

router = APIRouter(prefix="/appointments", tags=["appointments"])
scheduling_router = APIRouter(prefix="/scheduling", tags=["scheduling"])

WRITE_ROLES = (UserRole.owner, UserRole.admin, UserRole.member)
ADMIN_ROLES = (UserRole.owner, UserRole.admin)


def _decorate(a: Appointment, db: Session) -> dict:
    data = AppointmentOut.model_validate(a).model_dump()
    lead = db.get(Lead, a.lead_id)
    data["lead_business_name"] = lead.business_name if lead else None
    data["lead_email"] = lead.email if lead else None
    data["lead_phone"] = lead.phone if lead else None
    return data


def _get(db: Session, wid: uuid.UUID, appt_id: uuid.UUID) -> Appointment:
    return get_or_404(db, Appointment, appt_id, wid)


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


# ---------------------------------------------------------------- queries


@router.get("", response_model=Page[AppointmentOut])
def list_appointments(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status_: AppointmentStatus | None = Query(None, alias="status"),
    appointment_type: AppointmentType | None = None,
    lead_id: uuid.UUID | None = None,
    assigned_to: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    wid = auth.workspace.id
    query = select(Appointment).where(Appointment.workspace_id == wid)
    if status_:
        query = query.where(Appointment.status == status_)
    if appointment_type:
        query = query.where(Appointment.appointment_type == appointment_type)
    if lead_id:
        query = query.where(Appointment.lead_id == lead_id)
    if assigned_to:
        query = query.where(Appointment.assigned_to == assigned_to)
    if date_from:
        query = query.where(Appointment.end_at >= datetime.combine(
            date_from, datetime.min.time(), tzinfo=timezone.utc))
    if date_to:
        query = query.where(Appointment.start_at < datetime.combine(
            date_to + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc))
    if q:
        query = query.join(Lead, Lead.id == Appointment.lead_id).where(
            or_(Lead.business_name.ilike(f"%{q.strip()}%"),
                Appointment.title.ilike(f"%{q.strip()}%")))
    query = query.order_by(Appointment.start_at)
    items, total, page, page_size = paginate(db, query, page, page_size)
    return Page.build([AppointmentOut(**_decorate(a, db)) for a in items],
                      total, page, page_size)


@router.get("/dashboard", response_model=AssistantDashboard)
def assistant_dashboard(auth: AuthContext = Depends(get_current_auth),
                        db: Session = Depends(get_db)):
    stats = appt_svc.dashboard_stats(db, auth.workspace.id)
    return AssistantDashboard(**stats)


@router.get("/availability/slots", response_model=list[SlotOut])
def availability_slots(
    date_from: date | None = None,
    date_to: date | None = None,
    duration_minutes: int | None = Query(default=None, ge=5, le=480),
    assigned_to: str = "primary",
    limit: int = Query(default=60, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: Session = Depends(get_db),
):
    slots = availability.available_slots(
        db, auth.workspace.id, start_local=date_from, end_local=date_to,
        duration_minutes=duration_minutes, assigned_to=assigned_to, limit=limit)
    return [SlotOut(**s) for s in slots]


@router.post("/propose", response_model=AppointmentOut,
             status_code=status.HTTP_201_CREATED)
def propose_appointment(body: AppointmentPropose,
                        auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                        db: Session = Depends(get_db)):
    wid = auth.workspace.id
    get_or_404(db, Lead, body.lead_id, wid)
    agent = db.scalar(select(Agent).where(
        Agent.workspace_id == wid, Agent.slug == "executive-assistant"))
    try:
        appt = appt_svc.propose_slots(
            db, wid, lead_id=body.lead_id,
            appointment_type=body.appointment_type, title=body.title,
            conversation_id=body.conversation_id,
            source_agent_id=agent.id if agent else None,
            duration_minutes=body.duration_minutes, slots=body.slots)
    except appt_svc.BookingConflict as exc:
        raise _conflict(exc)
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
def create_appointment(body: AppointmentCreate,
                       auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                       db: Session = Depends(get_db)):
    wid = auth.workspace.id
    get_or_404(db, Lead, body.lead_id, wid)
    data = body.model_dump()
    data["appointment_type"] = body.appointment_type
    try:
        appt = appt_svc.create_appointment(
            db, wid, **{k: v for k, v in data.items()
                        if k in ("lead_id", "appointment_type", "title", "description",
                                 "start_at", "end_at", "contact_id", "conversation_id",
                                 "location", "meeting_link", "assigned_to", "status",
                                 "proposed_slots")},
            source="manual", actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.BookingConflict as exc:
        raise _conflict(exc)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.get("/{appointment_id}", response_model=AppointmentOut)
def get_appointment(appointment_id: uuid.UUID,
                    auth: AuthContext = Depends(get_current_auth),
                    db: Session = Depends(get_db)):
    appt = _get(db, auth.workspace.id, appointment_id)
    return AppointmentOut(**_decorate(appt, db))


@router.patch("/{appointment_id}", response_model=AppointmentOut)
def update_appointment(appointment_id: uuid.UUID, body: AppointmentUpdate,
                       auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                       db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    if appt.status in (AppointmentStatus.cancelled, AppointmentStatus.completed,
                       AppointmentStatus.no_show):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Cannot edit a {appt.status.value} appointment")
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        if field == "appointment_type":
            value = AppointmentType(value)
        setattr(appt, field, value)
    log_activity(db, wid, "appointment.updated", lead_id=appt.lead_id,
                 actor_id=auth.user.id, actor_type=ActorType.user,
                 description=f"Appointment '{appt.title}' updated",
                 metadata={"appointment_id": str(appt.id), "fields": list(data.keys())})
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/confirm", response_model=AppointmentOut)
def confirm_appointment(appointment_id: uuid.UUID,
                        auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                        db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.confirm_appointment(db, wid, appt,
                                     actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.BookingConflict as exc:
        raise _conflict(exc)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/book-slot", response_model=AppointmentOut)
def book_slot(appointment_id: uuid.UUID, body: AppointmentBookSlot,
              auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
              db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.book_slot(db, wid, appt, body.start, body.end,
                           actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.BookingConflict as exc:
        raise _conflict(exc)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule_appointment(appointment_id: uuid.UUID, body: AppointmentReschedule,
                           auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                           db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.apply_reschedule(db, wid, appt, body.start_at, body.end_at,
                                  reason=body.reason,
                                  actor_type=ActorType.user, actor_id=auth.user.id)
        if appt.status == AppointmentStatus.rescheduling_requested:
            # require_confirmation policy: move to awaiting_confirmation
            from app.services import availability as avail
            settings = avail.get_settings(db, wid)
            if settings.require_confirmation:
                appt.status = AppointmentStatus.awaiting_confirmation
    except appt_svc.BookingConflict as exc:
        raise _conflict(exc)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/request-reschedule", response_model=AppointmentOut)
def request_reschedule(appointment_id: uuid.UUID,
                       auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                       db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.request_reschedule(db, wid, appt,
                                    actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel_appointment(appointment_id: uuid.UUID, body: AppointmentCancel,
                       auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                       db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    appt_svc.cancel_appointment(db, wid, appt, reason=body.reason,
                                cancelled_by="user", actor_type=ActorType.user,
                                actor_id=auth.user.id)
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/complete", response_model=AppointmentOut)
def complete_appointment(appointment_id: uuid.UUID,
                         auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                         db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.complete_appointment(db, wid, appt,
                                      actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/no-show", response_model=AppointmentOut)
def no_show(appointment_id: uuid.UUID,
            auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
            db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    try:
        appt_svc.mark_no_show(db, wid, appt,
                              actor_type=ActorType.user, actor_id=auth.user.id)
    except appt_svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    db.refresh(appt)
    return AppointmentOut(**_decorate(appt, db))


@router.post("/{appointment_id}/sync-calendar", response_model=dict)
def sync_calendar(appointment_id: uuid.UUID,
                  auth: AuthContext = Depends(require_roles(*WRITE_ROLES)),
                  db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    result = appt_svc.sync_calendar(db, wid, appt)
    db.commit()
    return result


@router.get("/{appointment_id}/reminders", response_model=list[AppointmentReminderOut])
def list_reminders(appointment_id: uuid.UUID,
                   auth: AuthContext = Depends(get_current_auth),
                   db: Session = Depends(get_db)):
    wid = auth.workspace.id
    appt = _get(db, wid, appointment_id)
    rows = db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.appointment_id == appt.id
    ).order_by(AppointmentReminder.remind_at)).all()
    return [AppointmentReminderOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------- scheduling settings


@scheduling_router.get("/settings", response_model=SchedulingSettingsOut)
def get_scheduling_settings(auth: AuthContext = Depends(get_current_auth),
                            db: Session = Depends(get_db)):
    return availability.get_settings(db, auth.workspace.id)


@scheduling_router.put("/settings", response_model=SchedulingSettingsOut)
def update_scheduling_settings(body: SchedulingSettingsUpdate,
                               auth: AuthContext = Depends(require_roles(*ADMIN_ROLES)),
                               db: Session = Depends(get_db)):
    wid = auth.workspace.id
    data = body.model_dump(exclude_unset=True)
    if "working_days" in data and data["working_days"] is not None:
        data["working_days"] = sorted({int(d) for d in data["working_days"]
                                       if 0 <= int(d) <= 6})
    row = availability.update_settings(db, wid, data)
    log_activity(db, wid, "scheduling.settings_updated", actor_id=auth.user.id,
                 actor_type=ActorType.user,
                 description="Scheduling/availability settings updated",
                 metadata={"fields": list(data.keys())})
    db.commit()
    db.refresh(row)
    return row


@scheduling_router.get("/providers")
def scheduling_providers(auth: AuthContext = Depends(get_current_auth)):
    """Calendar provider status — configured or not, never exposes secrets."""
    providers = available_providers()
    return {"providers": providers, "internal_engine": "always available"}

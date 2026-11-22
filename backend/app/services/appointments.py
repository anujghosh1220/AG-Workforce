"""Appointment management service — booking, lifecycle, calendar sync.

Transaction-safe rules:
- Every appointment belongs to exactly one workspace.
- Concrete bookings are validated against the internal availability engine
  (working hours, breaks, buffers, conflicts) before being persisted.
- Status transitions are explicit; history is preserved in reschedule_history
  and the activity log.
- External calendar sync only reports success when the provider confirms.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.calendar.base import CalendarEvent, CalendarNotConfigured, CalendarProviderError
from app.integrations.calendar.registry import get_calendar_provider
from app.models import (
    ActorType,
    Agent,
    Appointment,
    AppointmentStatus,
    AppointmentType,
    AssigneeType,
    CalendarSyncStatus,
    Channel,
    Conversation,
    Lead,
    LeadStage,
    NotificationType,
    Task,
    TaskPriority,
    TaskStatus,
)
from app.services import availability, reminders
from app.services.helpers import log_activity, notify_workspace

ACTIVE_STATUSES = {
    AppointmentStatus.proposed,
    AppointmentStatus.awaiting_confirmation,
    AppointmentStatus.confirmed,
    AppointmentStatus.rescheduling_requested,
}


class BookingConflict(Exception):
    pass


class InvalidTransition(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _block_window(settings, start_utc: datetime, end_utc: datetime):
    """Occupied interval incl. configured buffer — mirrors the overlap rule
    used by availability.is_slot_available: [start-buffer, end+buffer)."""
    buf = timedelta(minutes=settings.buffer_minutes or 0)
    return start_utc - buf, end_utc + buf


def _sync_block(appt: Appointment, settings) -> None:
    if appt.block_start is None or appt.start_at is None:
        return
    s = appt.start_at if appt.start_at.tzinfo else appt.start_at.replace(tzinfo=timezone.utc)
    e = appt.end_at if appt.end_at.tzinfo else appt.end_at.replace(tzinfo=timezone.utc)
    appt.block_start, appt.block_end = _block_window(settings, s, e)


def _parse_dt(value, tz_name: str = "Asia/Kolkata") -> datetime:
    """Parse ISO datetime; naive values are interpreted in the workspace tz."""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        try:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        except Exception:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ea_agent(db: Session, wid: uuid.UUID) -> Agent | None:
    return db.scalar(select(Agent).where(
        Agent.workspace_id == wid, Agent.slug == "executive-assistant"))


def _agent_by_slug(db: Session, wid: uuid.UUID, slug: str) -> Agent | None:
    return db.scalar(select(Agent).where(
        Agent.workspace_id == wid, Agent.slug == slug))


def _default_title(appt_type: AppointmentType, lead: Lead | None) -> str:
    labels = {
        AppointmentType.product_demo: "Product demo",
        AppointmentType.discovery_call: "Discovery call",
        AppointmentType.sales_consultation: "Sales consultation",
        AppointmentType.follow_up: "Follow-up meeting",
        AppointmentType.onboarding: "Onboarding meeting",
        AppointmentType.internal: "Internal meeting",
    }
    base = labels.get(appt_type, "Meeting")
    return f"{base} — {lead.business_name}" if lead else base


def create_appointment(
    db: Session,
    wid: uuid.UUID,
    *,
    lead_id: uuid.UUID,
    appointment_type: AppointmentType = AppointmentType.product_demo,
    title: str | None = None,
    description: str = "",
    start_at=None,
    end_at=None,
    contact_id: uuid.UUID | None = None,
    conversation_id: uuid.UUID | None = None,
    location: str | None = None,
    meeting_link: str | None = None,
    assigned_to: str = "primary",
    status: AppointmentStatus = AppointmentStatus.confirmed,
    proposed_slots: list | None = None,
    source: str = "manual",
    source_agent_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    actor_type: ActorType = ActorType.user,
    actor_id: uuid.UUID | None = None,
    check_availability: bool = True,
    auto_finalize: bool = True,
) -> Appointment:
    """Create an appointment. Raises BookingConflict when the slot is taken."""
    settings = availability.get_settings(db, wid)
    lead = db.get(Lead, lead_id)
    if lead is None or lead.workspace_id != wid:
        raise ValueError("Lead not found in workspace")

    if idempotency_key:
        existing = db.scalar(select(Appointment).where(
            Appointment.workspace_id == wid,
            Appointment.idempotency_key == idempotency_key))
        if existing:
            return existing

    start_utc = end_utc = None
    if start_at is not None and end_at is not None:
        start_utc = _parse_dt(start_at, settings.timezone)
        end_utc = _parse_dt(end_at, settings.timezone)
        if check_availability and status in (AppointmentStatus.confirmed,
                                             AppointmentStatus.awaiting_confirmation):
            ok, reason = availability.is_slot_available(
                db, wid, start_utc, end_utc, assigned_to=assigned_to)
            if not ok:
                raise BookingConflict(reason or "slot unavailable")
    elif status in (AppointmentStatus.confirmed, AppointmentStatus.awaiting_confirmation):
        raise ValueError("start_at and end_at are required for confirmed bookings")

    appt = Appointment(
        workspace_id=wid,
        lead_id=lead_id,
        block_start=None, block_end=None,
        contact_id=contact_id,
        conversation_id=conversation_id,
        appointment_type=appointment_type,
        title=title or _default_title(appointment_type, lead),
        description=description,
        start_at=start_utc or _now(),
        end_at=end_utc or (_now() + timedelta(minutes=settings.default_duration_minutes)),
        timezone=settings.timezone,
        location=location,
        meeting_link=meeting_link,
        assigned_to=assigned_to,
        status=status,
        proposed_slots=proposed_slots or [],
        source=source,
        source_agent_id=source_agent_id,
        idempotency_key=idempotency_key,
        calendar_provider=settings.calendar_provider or "internal",
    )
    _sync_block(appt, settings)
    db.add(appt)
    db.flush()

    if status == AppointmentStatus.confirmed:
        appt.confirmed_at = _now()
        if auto_finalize:
            finalize_booking(db, wid, appt, actor_type=actor_type, actor_id=actor_id)
    elif status == AppointmentStatus.awaiting_confirmation:
        # awaiting_confirmation still needs reminders once confirmed later
        pass

    log_activity(db, wid, "appointment.created", lead_id=lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' created ({appt.status.value})",
                 metadata={"appointment_id": str(appt.id), "start": appt.start_at.isoformat()})
    db.flush()
    return appt


def finalize_booking(db: Session, wid: uuid.UUID, appt: Appointment,
                     actor_type: ActorType = ActorType.system,
                     actor_id: uuid.UUID | None = None) -> None:
    """Post-confirmation side effects: reminders, calendar sync, CRM + agent tasks."""
    if appt.confirmed_at is None:
        appt.confirmed_at = _now()
    reminders.schedule_reminders(db, wid, appt)
    sync_calendar(db, wid, appt)

    # CRM: move lead forward when a demo/meeting is confirmed.
    lead = db.get(Lead, appt.lead_id)
    if lead and lead.stage in (LeadStage.discovered, LeadStage.researched,
                               LeadStage.qualified, LeadStage.approval_pending,
                               LeadStage.contacted, LeadStage.replied,
                               LeadStage.interested):
        lead.stage = LeadStage.demo_scheduled

    conv = db.get(Conversation, appt.conversation_id) if appt.conversation_id else None
    if conv and conv.state not in ("opted_out", "closed", "human_handoff"):
        conv.state = "demo_scheduled"
        conv.last_intent = conv.last_intent or "demo_booking"

    # Agent coordination — deduplicated by open task with same title.
    _ensure_task(db, wid, appt, "sales-executive",
                 f"Prepare briefing for {lead.business_name if lead else 'prospect'}",
                 f"Demo/meeting '{appt.title}' on {appt.start_at.isoformat()}. "
                 f"Prepare a prospect briefing and approved sales introduction.")
    _ensure_task(db, wid, appt, "communication-manager",
                 f"Send booking confirmation to {lead.business_name if lead else 'prospect'}",
                 f"Confirm '{appt.title}' ({appt.start_at.isoformat()} {appt.timezone}) "
                 f"via the active conversation channel.")

    notify_workspace(db, wid, NotificationType.upcoming_meeting,
                     f"Appointment confirmed: {appt.title}",
                     body=f"{appt.start_at.isoformat()} ({appt.timezone})",
                     link="/calendar")
    log_activity(db, wid, "appointment.confirmed", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' confirmed for {appt.start_at.isoformat()}",
                 metadata={"appointment_id": str(appt.id)})


def _ensure_task(db: Session, wid: uuid.UUID, appt: Appointment, slug: str,
                 title: str, description: str) -> None:
    agent = _agent_by_slug(db, wid, slug)
    exists = db.scalar(select(Task.id).where(
        Task.workspace_id == wid,
        Task.lead_id == appt.lead_id,
        Task.title == title,
        Task.status.in_([TaskStatus.pending, TaskStatus.in_progress]),
    ))
    if exists or agent is None:
        return
    db.add(Task(
        workspace_id=wid, title=title, description=description,
        assignee_type=AssigneeType.agent, assignee_agent_id=agent.id,
        priority=TaskPriority.medium, status=TaskStatus.pending,
        lead_id=appt.lead_id, due_date=appt.start_at,
    ))


def confirm_appointment(db: Session, wid: uuid.UUID, appt: Appointment,
                        actor_type: ActorType = ActorType.user,
                        actor_id: uuid.UUID | None = None) -> Appointment:
    if appt.status not in (AppointmentStatus.proposed,
                           AppointmentStatus.awaiting_confirmation,
                           AppointmentStatus.rescheduling_requested):
        raise InvalidTransition(f"Cannot confirm appointment in status {appt.status.value}")
    # Re-validate the slot — it may have been taken since proposal.
    ok, reason = availability.is_slot_available(
        db, wid, appt.start_at, appt.end_at,
        assigned_to=appt.assigned_to, exclude_id=appt.id)
    if not ok:
        raise BookingConflict(reason or "slot no longer available")
    appt.status = AppointmentStatus.confirmed
    appt.confirmed_at = _now()
    finalize_booking(db, wid, appt, actor_type=actor_type, actor_id=actor_id)
    db.flush()
    return appt


def propose_slots(db: Session, wid: uuid.UUID, *, lead_id: uuid.UUID,
                  appointment_type: AppointmentType = AppointmentType.product_demo,
                  slots: list[dict] | None = None,
                  conversation_id: uuid.UUID | None = None,
                  source: str = "agent",
                  source_agent_id: uuid.UUID | None = None,
                  title: str | None = None,
                  duration_minutes: int | None = None) -> Appointment:
    """Create a proposed appointment offering `slots` (list of {start,end} ISO)."""
    settings = availability.get_settings(db, wid)
    if not slots:
        slots = availability.available_slots(db, wid, duration_minutes=duration_minutes,
                                             limit=5)
    if not slots:
        raise BookingConflict("No available slots in the booking horizon")
    first = slots[0]
    appt = create_appointment(
        db, wid, lead_id=lead_id, appointment_type=appointment_type,
        title=title, start_at=first["start"], end_at=first["end"],
        conversation_id=conversation_id, status=AppointmentStatus.proposed,
        proposed_slots=[{"start": s["start"], "end": s["end"]} for s in slots],
        source=source, source_agent_id=source_agent_id,
        check_availability=False, auto_finalize=False,
    )
    return appt


def book_slot(db: Session, wid: uuid.UUID, appt: Appointment, start, end,
              actor_type: ActorType = ActorType.user,
              actor_id: uuid.UUID | None = None) -> Appointment:
    """Commit the prospect's chosen slot. Never books an unavailable time."""
    if appt.status in (AppointmentStatus.cancelled, AppointmentStatus.completed,
                       AppointmentStatus.no_show):
        raise InvalidTransition(f"Cannot book slot for {appt.status.value} appointment")
    settings = availability.get_settings(db, wid)
    start_utc = _parse_dt(start, settings.timezone)
    end_utc = _parse_dt(end, settings.timezone)
    ok, reason = availability.is_slot_available(
        db, wid, start_utc, end_utc, assigned_to=appt.assigned_to,
        exclude_id=appt.id)
    if not ok:
        raise BookingConflict(reason or "slot unavailable")
    appt.start_at = start_utc
    appt.end_at = end_utc
    _sync_block(appt, settings)
    if appt.status == AppointmentStatus.proposed:
        appt.status = (AppointmentStatus.awaiting_confirmation
                       if settings.require_confirmation
                       else AppointmentStatus.confirmed)
    elif appt.status == AppointmentStatus.rescheduling_requested:
        appt.status = (AppointmentStatus.awaiting_confirmation
                       if settings.require_confirmation
                       else AppointmentStatus.confirmed)
    if appt.status == AppointmentStatus.confirmed:
        appt.confirmed_at = _now()
        finalize_booking(db, wid, appt, actor_type=actor_type, actor_id=actor_id)
    db.flush()
    return appt


def request_reschedule(db: Session, wid: uuid.UUID, appt: Appointment,
                       actor_type: ActorType = ActorType.user,
                       actor_id: uuid.UUID | None = None) -> Appointment:
    if appt.status not in (AppointmentStatus.confirmed,
                           AppointmentStatus.awaiting_confirmation):
        raise InvalidTransition(f"Cannot reschedule {appt.status.value} appointment")
    appt.status = AppointmentStatus.rescheduling_requested
    log_activity(db, wid, "appointment.reschedule_requested", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Reschedule requested for '{appt.title}'",
                 metadata={"appointment_id": str(appt.id)})
    db.flush()
    return appt


def apply_reschedule(db: Session, wid: uuid.UUID, appt: Appointment,
                     new_start, new_end, reason: str = "",
                     actor_type: ActorType = ActorType.user,
                     actor_id: uuid.UUID | None = None) -> Appointment:
    """Move an appointment to a new validated slot, preserving history."""
    if appt.status in (AppointmentStatus.cancelled, AppointmentStatus.completed,
                       AppointmentStatus.no_show):
        raise InvalidTransition(f"Cannot reschedule {appt.status.value} appointment")
    settings = availability.get_settings(db, wid)
    start_utc = _parse_dt(new_start, settings.timezone)
    end_utc = _parse_dt(new_end, settings.timezone)
    ok, conflict = availability.is_slot_available(
        db, wid, start_utc, end_utc, assigned_to=appt.assigned_to,
        exclude_id=appt.id)
    if not ok:
        raise BookingConflict(conflict or "new slot unavailable")

    history = list(appt.reschedule_history or [])
    history.append({
        "from_start": appt.start_at.isoformat(),
        "from_end": appt.end_at.isoformat(),
        "to_start": start_utc.isoformat(),
        "to_end": end_utc.isoformat(),
        "reason": reason,
        "at": _now().isoformat(),
        "by": actor_type.value,
    })
    appt.reschedule_history = history
    appt.start_at = start_utc
    appt.end_at = end_utc
    _sync_block(appt, settings)
    if appt.status == AppointmentStatus.rescheduling_requested:
        appt.status = (AppointmentStatus.awaiting_confirmation
                       if settings.require_confirmation
                       else AppointmentStatus.confirmed)
    elif appt.status == AppointmentStatus.proposed:
        pass  # keep proposed; slot updated

    if appt.status == AppointmentStatus.confirmed:
        appt.confirmed_at = _now()
        reminders.reschedule_reminders(db, wid, appt)
        sync_calendar(db, wid, appt)
        notify_workspace(db, wid, NotificationType.upcoming_meeting,
                         f"Appointment rescheduled: {appt.title}",
                         body=f"Now {start_utc.isoformat()} ({appt.timezone})",
                         link="/calendar")

    log_activity(db, wid, "appointment.rescheduled", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' moved to {start_utc.isoformat()}",
                 metadata={"appointment_id": str(appt.id), "reason": reason})
    db.flush()
    return appt


def cancel_appointment(db: Session, wid: uuid.UUID, appt: Appointment,
                       reason: str = "", cancelled_by: str = "user",
                       actor_type: ActorType = ActorType.user,
                       actor_id: uuid.UUID | None = None) -> Appointment:
    if appt.status == AppointmentStatus.cancelled:
        return appt
    appt.status = AppointmentStatus.cancelled
    appt.cancelled_at = _now()
    appt.cancelled_by = cancelled_by
    appt.cancellation_reason = reason
    reminders.cancel_reminders(db, wid, appt)
    _cancel_calendar_event(db, wid, appt)

    lead = db.get(Lead, appt.lead_id)
    if lead and lead.stage == LeadStage.demo_scheduled:
        lead.stage = LeadStage.interested
    conv = db.get(Conversation, appt.conversation_id) if appt.conversation_id else None
    if conv and conv.state == "demo_scheduled":
        conv.state = "engaged"

    notify_workspace(db, wid, NotificationType.system,
                     f"Appointment cancelled: {appt.title}",
                     body=reason or "No reason given", link="/calendar")
    log_activity(db, wid, "appointment.cancelled", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' cancelled ({cancelled_by})",
                 metadata={"appointment_id": str(appt.id), "reason": reason})
    db.flush()
    return appt


def complete_appointment(db: Session, wid: uuid.UUID, appt: Appointment,
                         actor_type: ActorType = ActorType.user,
                         actor_id: uuid.UUID | None = None) -> Appointment:
    if appt.status not in (AppointmentStatus.confirmed,
                           AppointmentStatus.awaiting_confirmation):
        raise InvalidTransition(f"Cannot complete {appt.status.value} appointment")
    appt.status = AppointmentStatus.completed
    appt.completed_at = _now()
    reminders.cancel_reminders(db, wid, appt)
    log_activity(db, wid, "appointment.completed", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' marked completed",
                 metadata={"appointment_id": str(appt.id)})
    db.flush()
    return appt


def mark_no_show(db: Session, wid: uuid.UUID, appt: Appointment,
                 actor_type: ActorType = ActorType.user,
                 actor_id: uuid.UUID | None = None) -> Appointment:
    if appt.status != AppointmentStatus.confirmed:
        raise InvalidTransition("Only confirmed appointments can be marked no-show")
    appt.status = AppointmentStatus.no_show
    reminders.cancel_reminders(db, wid, appt)
    log_activity(db, wid, "appointment.no_show", lead_id=appt.lead_id,
                 actor_type=actor_type, actor_id=actor_id,
                 description=f"Appointment '{appt.title}' marked no-show",
                 metadata={"appointment_id": str(appt.id)})
    db.flush()
    return appt


# ---------------------------------------------------------------- calendar sync


def _calendar_event_for(appt: Appointment, lead: Lead | None) -> CalendarEvent:
    desc = appt.description or ""
    if lead:
        desc = (f"{lead.business_name}" + (f" — {desc}" if desc else "")).strip(" —")
    return CalendarEvent(
        summary=appt.title,
        description=desc,
        start=appt.start_at,
        end=appt.end_at,
        location=appt.meeting_link or appt.location,
        timezone=appt.timezone,
    )


def sync_calendar(db: Session, wid: uuid.UUID, appt: Appointment) -> dict:
    """Push appointment state to the configured external calendar provider.

    Returns a truthful status dict — never claims success without provider
    confirmation. Safe to call repeatedly (idempotent via stored event id and
    provider-side idempotency key).
    """
    settings = availability.get_settings(db, wid)
    provider_name = appt.calendar_provider or settings.calendar_provider or "internal"
    if provider_name in ("", "internal", "none"):
        appt.calendar_sync = CalendarSyncStatus.not_configured
        return {"synced": False, "reason": "no external calendar configured"}

    provider = get_calendar_provider(provider_name)
    if provider is None:
        appt.calendar_sync = CalendarSyncStatus.not_configured
        appt.sync_error = f"provider '{provider_name}' not configured"
        return {"synced": False, "reason": appt.sync_error}

    try:
        lead = db.get(Lead, appt.lead_id)
        event = _calendar_event_for(appt, lead)
        key = appt.idempotency_key or f"agw-appt-{appt.id}"
        if appt.status in (AppointmentStatus.cancelled, AppointmentStatus.no_show,
                           AppointmentStatus.completed):
            if appt.external_event_id:
                provider.cancel_event(appt.external_event_id)
            appt.calendar_sync = CalendarSyncStatus.synced
            appt.last_synced_at = _now()
            appt.sync_error = None
            return {"synced": True, "event_id": appt.external_event_id}

        if appt.external_event_id:
            res = provider.update_event(appt.external_event_id, event)
        else:
            res = provider.create_event(event, idempotency_key=key)
        appt.external_event_id = res.event_id or appt.external_event_id
        appt.calendar_sync = CalendarSyncStatus.synced
        appt.last_synced_at = _now()
        appt.sync_error = None
        return {"synced": True, "event_id": appt.external_event_id}
    except CalendarNotConfigured as exc:
        appt.calendar_sync = CalendarSyncStatus.not_configured
        appt.sync_error = str(exc)[:500]
        return {"synced": False, "reason": appt.sync_error}
    except (CalendarProviderError, Exception) as exc:  # noqa: BLE001
        appt.calendar_sync = CalendarSyncStatus.failed
        appt.sync_error = str(exc)[:500]
        return {"synced": False, "reason": appt.sync_error}


def _cancel_calendar_event(db: Session, wid: uuid.UUID, appt: Appointment) -> None:
    if not appt.external_event_id:
        return
    settings = availability.get_settings(db, wid)
    provider = get_calendar_provider(appt.calendar_provider or settings.calendar_provider)
    if provider is None:
        return
    try:
        provider.cancel_event(appt.external_event_id)
        appt.calendar_sync = CalendarSyncStatus.synced
        appt.last_synced_at = _now()
    except Exception as exc:  # noqa: BLE001
        appt.calendar_sync = CalendarSyncStatus.failed
        appt.sync_error = str(exc)[:500]


# ---------------------------------------------------------------- queries


def upcoming(db: Session, wid: uuid.UUID, limit: int = 20) -> list[Appointment]:
    return list(db.scalars(
        select(Appointment)
        .where(Appointment.workspace_id == wid,
               Appointment.status.in_(ACTIVE_STATUSES | {AppointmentStatus.completed}),
               Appointment.start_at >= _now() - timedelta(days=1))
        .order_by(Appointment.start_at)
        .limit(limit)
    ).all())


def dashboard_stats(db: Session, wid: uuid.UUID) -> dict:
    from sqlalchemy import func

    now = _now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today_start + timedelta(days=1)

    def count(*conds):
        return db.scalar(select(func.count(Appointment.id)).where(
            Appointment.workspace_id == wid, *conds)) or 0
    stats = {
        "upcoming_demos": count(Appointment.status.in_([AppointmentStatus.confirmed,
                                                        AppointmentStatus.awaiting_confirmation]),
                                Appointment.start_at >= now,
                                Appointment.appointment_type == AppointmentType.product_demo),
        "today_meetings": count(Appointment.status.in_([AppointmentStatus.confirmed,
                                                        AppointmentStatus.awaiting_confirmation]),
                                Appointment.start_at >= today_start,
                                Appointment.start_at < tomorrow),
        "pending_booking_requests": count(Appointment.status == AppointmentStatus.proposed),
        "rescheduling_requests": count(Appointment.status == AppointmentStatus.rescheduling_requested),
        "cancellations": count(Appointment.status == AppointmentStatus.cancelled,
                               Appointment.cancelled_at >= now - timedelta(days=30)),
        "no_shows": count(Appointment.status == AppointmentStatus.no_show),
        "completed_demos": count(Appointment.status == AppointmentStatus.completed),
    }
    stats["calendar_provider"] = (availability.get_settings(db, wid).calendar_provider or "internal")
    return stats

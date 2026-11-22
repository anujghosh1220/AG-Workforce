"""Appointment reminder service.

- Reminders are rows keyed by (appointment, remind_at, channel) — the unique
  constraint makes scheduling idempotent and prevents duplicates.
- Rescheduling cancels pending reminders and recreates them for the new time.
- Delivery prefers the configured email/WhatsApp provider; when no provider is
  configured the reminder becomes an internal workspace notification and is
  marked `internal_notified` — never reported as externally delivered.
- Channel consent/suppression is enforced via the compliance module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    AppointmentReminder,
    AppointmentStatus,
    Channel,
    Conversation,
    Lead,
    MessageDirection,
    MessageStatus,
    NotificationType,
    ReminderStatus,
)
from app.services import availability, compliance
from app.services.helpers import notify_workspace


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime, tz_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo(tz_name)).strftime("%a %d %b %Y, %H:%M")
    except Exception:
        return dt.isoformat()


def _upcoming_statuses() -> set:
    return {AppointmentStatus.confirmed, AppointmentStatus.awaiting_confirmation}


def schedule_reminders(db: Session, wid: uuid.UUID, appt: Appointment) -> list[AppointmentReminder]:
    """(Re)create pending reminders for a confirmed/upcoming appointment."""
    if appt.status not in _upcoming_statuses():
        return []
    settings = availability.get_settings(db, wid)
    offsets = settings.reminder_offsets_minutes or []
    # Determine preferred channels: conversation channel if present else internal.
    channels = _channels_for(db, appt)
    created: list[AppointmentReminder] = []
    for minutes in offsets:
        remind_at = appt.start_at - timedelta(minutes=int(minutes))
        for channel in channels:
            # dedupe: skip if an equivalent reminder already exists
            exists = db.scalar(select(AppointmentReminder.id).where(
                AppointmentReminder.appointment_id == appt.id,
                AppointmentReminder.remind_at == remind_at,
                AppointmentReminder.channel == channel,
                AppointmentReminder.status.in_([ReminderStatus.pending,
                                                ReminderStatus.sent,
                                                ReminderStatus.internal_notified]),
            ))
            if exists:
                continue
            r = AppointmentReminder(
                workspace_id=wid, appointment_id=appt.id,
                remind_at=remind_at, channel=channel,
                status=ReminderStatus.pending,
            )
            db.add(r)
            created.append(r)
    db.flush()
    return created


def _channels_for(db: Session, appt: Appointment) -> list[str]:
    conv = db.get(Conversation, appt.conversation_id) if appt.conversation_id else None
    if conv is not None:
        return [conv.channel.value]  # email | whatsapp
    return ["internal"]


def cancel_reminders(db: Session, wid: uuid.UUID, appt: Appointment) -> int:
    rows = db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.appointment_id == appt.id,
        AppointmentReminder.status == ReminderStatus.pending,
    )).all()
    for r in rows:
        r.status = ReminderStatus.cancelled
    db.flush()
    return len(rows)


def reschedule_reminders(db: Session, wid: uuid.UUID, appt: Appointment) -> list[AppointmentReminder]:
    cancel_reminders(db, wid, appt)
    return schedule_reminders(db, wid, appt)


def process_due_reminders(db: Session, now: datetime | None = None,
                          limit: int = 50) -> int:
    """Deliver all due pending reminders. Returns number processed."""
    now = now or _now()
    due = db.scalars(select(AppointmentReminder).where(
        AppointmentReminder.status == ReminderStatus.pending,
        AppointmentReminder.remind_at <= now,
    ).order_by(AppointmentReminder.remind_at).limit(limit)).all()

    processed = 0
    for r in due:
        appt = db.get(Appointment, r.appointment_id)
        if appt is None or appt.status not in _upcoming_statuses():
            r.status = ReminderStatus.cancelled
            continue
        _deliver(db, r, appt)
        processed += 1
    db.flush()
    return processed


def _deliver(db: Session, r: AppointmentReminder, appt: Appointment) -> None:
    settings = availability.get_settings(db, r.workspace_id)
    lead = db.get(Lead, appt.lead_id)
    when = _fmt(appt.start_at, appt.timezone or settings.timezone)
    title = f"Reminder: {appt.title}"
    body = (f"Appointment '{appt.title}'"
            + (f" with {lead.business_name}" if lead else "")
            + f" is scheduled for {when} ({appt.timezone}).")

    channel = r.channel
    if channel in (Channel.email.value, Channel.whatsapp.value):
        conv = db.get(Conversation, appt.conversation_id) if appt.conversation_id else None
        contact = lead.email if channel == Channel.email.value else lead.phone if lead else None
        # consent/suppression gate
        reason = compliance.outbound_block_reason(
            db, r.workspace_id, Channel(channel), value=contact,
            lead_id=appt.lead_id) if contact else "no contact address"
        if reason:
            r.status = ReminderStatus.failed
            r.last_error = f"compliance: {reason}"
            return
        provider = None
        try:
            if channel == Channel.email.value:
                from app.integrations.email.base import get_email_provider, OutboundEmail
                provider = get_email_provider()
                if provider is not None:
                    provider.send(OutboundEmail(to=contact, subject=title, body=body))
                    r.delivery_detail = f"email via {provider.name}"
            else:
                from app.integrations.whatsapp.base import get_whatsapp_provider, WhatsAppMessage
                provider = get_whatsapp_provider()
                if provider is not None:
                    provider.send_text(WhatsAppMessage(to=contact or "", body=body))
                    r.delivery_detail = f"whatsapp via {provider.name}"
        except Exception as exc:  # noqa: BLE001
            r.status = ReminderStatus.failed
            r.last_error = str(exc)[:400]
            return
        if provider is not None:
            r.status = ReminderStatus.sent
            r.sent_at = _now()
            return

    # No external provider (or internal channel): honest internal notification.
    notify_workspace(db, r.workspace_id, NotificationType.upcoming_meeting,
                     title, body=body, link="/calendar")
    r.status = ReminderStatus.internal_notified
    r.delivery_detail = "internal notification (no external provider configured)"
    r.sent_at = _now()

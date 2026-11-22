"""Executive Assistant scheduling intent handler.

Interprets prospect messages about demos/meetings inside the unified
conversation engine:

- demo/meeting requests        -> check availability, propose slots
- explicit time selection      -> re-validate and book (never an unavailable slot)
- reschedule requests          -> offer/validate new times, keep history
- cancellations               -> cancel appointment + reminders
- ambiguous dates/times        -> ask for clarification, never guess

All replies are returned as text; the caller persists them as outbound drafts
through the normal approval/channel pipeline.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActorType,
    Agent,
    Appointment,
    AppointmentStatus,
    AppointmentType,
    Channel,
    Conversation,
    Lead,
    LeadStage,
)
from app.services import appointments as appt_svc
from app.services import availability

WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

SCHEDULING_WORDS = [
    "demo", "demonstration", "meeting", "appointment", "schedule", "book",
    "booking", "call", "consultation", "onboarding", "timeslot", "time slot",
    "availability", "available", "reschedule", "cancel", "move it", "another time",
]
CANCEL_WORDS = ["cancel", "cannot attend", "can't attend", "cant attend",
                "won't be able", "wont be able", "can't make it", "cannot make it",
                "not going to make", "call it off"]
RESCHEDULE_WORDS = ["reschedule", "move it", "move the", "change the", "another time",
                    "different time", "different day", "instead", "push it",
                    "can we do it", "can we meet", "can we schedule"]
ACCEPT_WORDS = ["works", "work for me", "that works", "sounds good", "perfect",
                "confirm", "confirmed", "yes", "yep", "yeah", "sure", "ok",
                "okay", "book it", "booked", "see you", "count me in", "first one",
                "second one", "third one"]
ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
             "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}


def _tz(settings) -> ZoneInfo:
    return availability.tz_of(settings)


def _active_appointment(db: Session, wid: uuid.UUID, lead_id: uuid.UUID) -> Appointment | None:
    return db.scalar(
        select(Appointment)
        .where(Appointment.workspace_id == wid,
               Appointment.lead_id == lead_id,
               Appointment.status.in_([AppointmentStatus.proposed,
                                       AppointmentStatus.awaiting_confirmation,
                                       AppointmentStatus.confirmed,
                                       AppointmentStatus.rescheduling_requested]))
        .order_by(Appointment.updated_at.desc())
        .limit(1)
    )


def _has(text: str, words: list[str]) -> bool:
    t = text.lower()
    return any(re.search(rf"\b{re.escape(w)}\b", t) for w in words)


def classify(body: str) -> str:
    """Return scheduling|cancel|reschedule|accept|other for a prospect message."""
    t = body.lower()
    if _has(t, CANCEL_WORDS):
        return "cancel"
    if _has(t, RESCHEDULE_WORDS):
        return "reschedule"
    if _has(t, ACCEPT_WORDS) or _parse_datetime_hint(t) is not None:
        return "accept"
    if _has(t, SCHEDULING_WORDS):
        return "scheduling"
    return "other"


# ---------------------------------------------------------------- time parsing


def _parse_datetime_hint(text: str, tz: ZoneInfo | None = None, now: datetime | None = None):
    """Best-effort parse of a prospect's stated day/time.

    Returns dict {date, hour, minute, confidence} or None. Never invents a
    time when only a vague part-of-day is given — those return confidence
    'low' so the caller can clarify.
    """
    now = now or datetime.now(tz or ZoneInfo("UTC"))
    t = text.lower()
    day: date | None = None
    hour: int | None = None
    minute = 0
    vague = False

    # --- day
    if "day after tomorrow" in t:
        day = (now + timedelta(days=2)).date()
    elif "tomorrow" in t:
        day = (now + timedelta(days=1)).date()
    elif "today" in t or "tonight" in t:
        day = now.date()
    else:
        m = re.search(r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", t)
        target = None
        next_week = False
        if m:
            target = WEEKDAYS[m.group(1)]
            next_week = True
        else:
            m2 = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b", t)
            if m2:
                target = WEEKDAYS[m2.group(1)]
        if target is not None:
            days_ahead = (target - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            if next_week and days_ahead <= 7:
                days_ahead += 7
            day = (now + timedelta(days=days_ahead)).date()

    # --- time
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
        elif "noon" in t or "midday" in t:
            hour, minute = 12, 0
        elif re.search(r"\b(morning|forenoon)\b", t):
            hour, minute, vague = 10, 0, True
        elif "afternoon" in t:
            hour, minute, vague = 14, 0, True
        elif re.search(r"\b(evening|tonight|night)\b", t):
            hour, minute, vague = 18, 0, True

    if day is None and hour is None:
        return None
    if day is None:
        day = now.date()
        if hour is not None and time(hour, minute) <= now.time():
            day = (now + timedelta(days=1)).date()
    return {"date": day, "hour": hour, "minute": minute, "vague": vague,
            "confidence": "low" if vague or hour is None else "high"}


# ---------------------------------------------------------------- helpers


def _fmt_slot(slot: dict, tz: ZoneInfo) -> str:
    start = datetime.fromisoformat(slot["start"]).astimezone(tz)
    end = datetime.fromisoformat(slot["end"]).astimezone(tz)
    return f"{start.strftime('%a %d %b')} {start.strftime('%I:%M %p').lstrip('0')}–{end.strftime('%I:%M %p').lstrip('0')}"


def _proposed_slot_label(appt: Appointment, tz: ZoneInfo) -> str:
    slots = appt.proposed_slots or []
    labels = [_fmt_slot(s, tz) for s in slots]
    return "; ".join(labels) if labels else _fmt_slot(
        {"start": appt.start_at.isoformat(), "end": appt.end_at.isoformat()}, tz)


def _slot_matches(slot: dict, hint: dict, tz: ZoneInfo) -> bool:
    start = datetime.fromisoformat(slot["start"]).astimezone(tz)
    return (start.date() == hint["date"] and start.hour == hint["hour"]
            and start.minute == hint["minute"])


def _find_slot_for_hint(appt: Appointment, hint: dict, tz: ZoneInfo) -> dict | None:
    for s in appt.proposed_slots or []:
        if _slot_matches(s, hint, tz):
            return s
    return None


def _ea_agent(db: Session, wid: uuid.UUID) -> Agent | None:
    return db.scalar(select(Agent).where(
        Agent.workspace_id == wid, Agent.slug == "executive-assistant"))


def _settings(db: Session, wid: uuid.UUID):
    return availability.get_settings(db, wid)


def _default_duration(db: Session, wid: uuid.UUID) -> int:
    return _settings(db, wid).default_duration_minutes or 30


# ---------------------------------------------------------------- main entry


def handle_scheduling_message(db: Session, wid: uuid.UUID,
                              conv: Conversation, lead: Lead,
                              body: str) -> dict | None:
    """Attempt to handle `body` as a scheduling conversation turn.

    Returns None when the message is not scheduling-related so the caller can
    fall back to the generic reply pipeline.
    """
    settings = _settings(db, wid)
    tz = _tz(settings)
    kind = classify(body)
    appt = _active_appointment(db, wid, lead.id)

    # Not scheduling-related and no open appointment -> let generic flow run.
    if kind == "other" and appt is None:
        return None
    if kind == "accept" and appt is None and _parse_datetime_hint(body, tz) is None:
        # bare "yes"/"sounds good" without an open booking is not scheduling
        return None
    if kind == "other":
        # conversation has an open appointment; only respond to explicit
        # scheduling verbs — otherwise let the normal reply flow continue.
        if not _has(body.lower(), SCHEDULING_WORDS + ACCEPT_WORDS + CANCEL_WORDS + RESCHEDULE_WORDS):
            return None
        kind = "accept" if _has(body.lower(), ACCEPT_WORDS) else "scheduling"

    agent = _ea_agent(db, wid)
    agent_id = agent.id if agent else None

    try:
        if kind == "cancel":
            return _handle_cancel(db, wid, conv, lead, appt, body, agent_id)
        if kind == "reschedule":
            return _handle_reschedule(db, wid, conv, lead, appt, body, settings, tz, agent_id)
        if kind == "accept":
            return _handle_accept(db, wid, conv, lead, appt, body, settings, tz, agent_id)
        return _handle_new_request(db, wid, conv, lead, body, settings, tz, agent_id)
    except appt_svc.BookingConflict as exc:
        return {"handled": True,
                "reply": f"Sorry — {exc}. Would you like me to suggest other times?",
                "kind": "conflict"}
    except Exception:
        # Never break inbound processing because of scheduling logic.
        import logging
        logging.getLogger("agworkforce.scheduling").exception(
            "scheduling handler failed")
        return None


# ---------------------------------------------------------------- branches


def _handle_cancel(db, wid, conv, lead, appt, body, agent_id) -> dict:
    if appt is None:
        return {"handled": True,
                "reply": "I don't see an upcoming appointment for you. "
                         "Would you like me to schedule one?",
                "kind": "cancel_none"}
    reason = body.strip()[:300]
    appt_svc.cancel_appointment(db, wid, appt, reason=reason,
                                cancelled_by="prospect", actor_type=ActorType.agent,
                                actor_id=agent_id)
    db.commit()
    return {"handled": True, "appointment_id": str(appt.id), "kind": "cancelled",
            "reply": f"Done — I've cancelled '{appt.title}' scheduled for "
                     f"{_fmt_when(appt, _tz(_settings(db, wid)))}. "
                     "Let me know if you'd like to pick a new time."}


def _handle_new_request(db, wid, conv, lead, body, settings, tz, agent_id) -> dict:
    # A demo/meeting request: propose concrete available slots.
    duration = _default_duration(db, wid)
    try:
        appt = appt_svc.propose_slots(
            db, wid, lead_id=lead.id,
            appointment_type=_infer_type(body),
            conversation_id=conv.id, source="conversation",
            source_agent_id=agent_id, duration_minutes=duration)
    except appt_svc.BookingConflict:
        return {"handled": True, "kind": "no_slots",
                "reply": "I don't have any open slots in the next few weeks. "
                         "Would you like me to arrange a callback instead?"}
    db.commit()
    return {"handled": True, "appointment_id": str(appt.id), "kind": "proposed",
            "reply": _offer_reply(appt, lead, tz)}


def _handle_accept(db, wid, conv, lead, appt, body, settings, tz, agent_id) -> dict:
    hint = _parse_datetime_hint(body, tz)

    if appt is not None and appt.status in (AppointmentStatus.proposed,
                                            AppointmentStatus.awaiting_confirmation,
                                            AppointmentStatus.rescheduling_requested):
        # ordinal selection: "the second one"
        low = body.lower()
        for word, idx in ORDINALS.items():
            if re.search(rf"\b{re.escape(word)}\b", low):
                slots = appt.proposed_slots or []
                if idx < len(slots):
                    s = slots[idx]
                    return _book_chosen(db, wid, conv, lead, appt, s, settings, tz, agent_id)
        # explicit day/time matching a proposed slot
        if hint and hint["confidence"] == "high":
            match = _find_slot_for_hint(appt, hint, tz)
            if match:
                return _book_chosen(db, wid, conv, lead, appt, match, settings, tz, agent_id)
            # time given but not among proposals -> check raw availability
            return _try_direct_booking(db, wid, conv, lead, hint, settings, tz, agent_id)
        # vague day only -> show that day's proposed slots
        if hint and appt.proposed_slots:
            day_slots = [s for s in appt.proposed_slots
                         if datetime.fromisoformat(s["start"]).astimezone(tz).date() == hint["date"]]
            if day_slots:
                labels = "; ".join(_fmt_slot(s, tz) for s in day_slots)
                return {"handled": True, "kind": "clarify",
                        "reply": f"For {hint['date'].strftime('%A %d %b')} I can offer: "
                                 f"{labels}. Which time works for you?"}
        # bare acceptance -> clarify rather than guess
        return {"handled": True, "kind": "clarify",
                "appointment_id": str(appt.id),
                "reply": "Happy to book that. Which of these times works best?\n"
                         + _proposed_slot_label(appt, tz)}

    if appt is not None and appt.status == AppointmentStatus.confirmed:
        if hint and hint["confidence"] == "high":
            return _try_direct_booking(db, wid, conv, lead, hint, settings, tz, agent_id,
                                       existing=appt)
        return {"handled": True, "kind": "clarify",
                "reply": f"You already have '{appt.title}' on "
                         f"{_fmt_when(appt, tz)}. Did you want to reschedule it "
                         "or book an additional meeting?"}

    # No open appointment but they accepted/gave a time -> try direct booking.
    if hint and hint["confidence"] == "high":
        return _try_direct_booking(db, wid, conv, lead, hint, settings, tz, agent_id)
    return {"handled": True, "kind": "clarify",
            "reply": "Sure — what day and time works for you? I can check "
                     "availability right away."}


def _handle_reschedule(db, wid, conv, lead, appt, body, settings, tz, agent_id) -> dict:
    if appt is None:
        return {"handle": True, "kind": "reschedule_none",
                "reply": "I don't see an upcoming appointment to move. "
                         "Would you like to schedule a new one?"}
    hint = _parse_datetime_hint(body, tz)

    if appt.status == AppointmentStatus.confirmed:
        appt_svc.request_reschedule(db, wid, appt, actor_type=ActorType.agent,
                                    actor_id=agent_id)

    if hint and hint["confidence"] == "high":
        # concrete new time -> validate and apply
        start_local = datetime.combine(hint["date"], time(hint["hour"], hint["minute"]),
                                       tzinfo=tz)
        duration = (appt.end_at - appt.start_at) if appt.end_at and appt.start_at else timedelta(
            minutes=settings.default_duration_minutes)
        try:
            appt_svc.apply_reschedule(
                db, wid, appt, start_local.isoformat(),
                (start_local + duration).isoformat(), reason=body[:200],
                actor_type=ActorType.agent, actor_id=agent_id)
            db.commit()
            if appt.status == AppointmentStatus.confirmed:
                reply = (f"Done — '{appt.title}' is now on "
                         f"{_fmt_when(appt, tz)}. I've updated everything on my side.")
            else:
                reply = (f"I've requested '{_fmt_when(appt, tz)}' for "
                         f"'{appt.title}'. I'll confirm as soon as it's approved.")
            return {"handled": True, "appointment_id": str(appt.id),
                    "kind": "rescheduled", "reply": reply}
        except appt_svc.BookingConflict as exc:
            # fall through to offering alternatives
            pass

    # Offer fresh slots for the new time.
    duration = int((appt.end_at - appt.start_at).total_seconds() // 60) \
        if appt.end_at and appt.start_at else settings.default_duration_minutes
    slots = availability.available_slots(db, wid, duration_minutes=duration, limit=5)
    appt.proposed_slots = [{"start": s["start"], "end": s["end"]} for s in slots]
    db.flush()
    if not slots:
        return {"handled": True, "kind": "no_slots",
                "reply": "I couldn't find another open slot right now. "
                         "Want me to keep the current time or check a later date?"}
    labels = "; ".join(_fmt_slot(s, tz) for s in slots)
    return {"handled": True, "appointment_id": str(appt.id), "kind": "reschedule_offer",
            "reply": f"No problem — here are some alternatives for "
                     f"'{appt.title}': {labels}. Which works for you?"}


def _book_chosen(db, wid, conv, lead, appt, slot, settings, tz, agent_id) -> dict:
    try:
        appt_svc.book_slot(db, wid, appt, slot["start"], slot["end"],
                           actor_type=ActorType.agent, actor_id=agent_id)
    except appt_svc.BookingConflict as exc:
        # book_slot validates before mutating, so nothing to roll back.
        return {"handled": True, "kind": "conflict",
                "reply": f"Sorry, that time was just taken — {exc}. "
                         "Here are other options:\n" + _proposed_slot_label(appt, tz)}
    db.commit()
    if appt.status == AppointmentStatus.confirmed:
        return {"handled": True, "appointment_id": str(appt.id), "kind": "booked",
                "reply": f"Perfect — '{appt.title}' is confirmed for "
                         f"{_fmt_when(appt, tz)}. I've sent you the details and "
                         "set a reminder. See you then!"}
    # awaiting human confirmation per workspace policy
    _request_booking_approval(db, wid, appt, agent_id)
    db.commit()
    return {"handled": True, "appointment_id": str(appt.id),
            "kind": "pending_approval",
            "reply": f"Great choice — I've requested '{_fmt_when(appt, tz)}' for "
                     f"'{appt.title}'. It's pending a quick internal approval; "
                     "I'll confirm with you as soon as it's cleared."}


def _request_booking_approval(db, wid, appt, agent_id) -> None:
    """Create an approval record for the booking confirmation (audit trail)."""
    from app.models import Approval, ApprovalActionType, ApprovalStatus, RiskLevel
    # JSON path queries are DB-specific; do a simple in-Python check instead.
    pendings = db.scalars(select(Approval).where(
        Approval.workspace_id == wid,
        Approval.action_type == ApprovalActionType.schedule_meeting,
        Approval.status == ApprovalStatus.pending,
    )).all()
    for a in pendings:
        if (a.payload or {}).get("appointment_id") == str(appt.id):
            return
    db.add(Approval(
        workspace_id=wid,
        action_type=ApprovalActionType.schedule_meeting,
        risk_level=RiskLevel.medium,
        status=ApprovalStatus.pending,
        title=f"Confirm appointment: {appt.title}",
        payload={"appointment_id": str(appt.id),
                 "start": appt.start_at.isoformat(),
                 "end": appt.end_at.isoformat(),
                 "lead_id": str(appt.lead_id)},
        requested_by_agent_id=agent_id,
        lead_id=appt.lead_id,
    ))
    db.flush()


def _try_direct_booking(db, wid, conv, lead, hint, settings, tz, agent_id,
                        existing: Appointment | None = None) -> dict:
    """Book an explicitly stated day/time after availability validation."""
    if hint["hour"] is None:
        # day given, no time -> offer that day's slots
        slots = availability.available_slots(db, wid, start_local=hint["date"],
                                             end_local=hint["date"], limit=8)
        if not slots:
            return {"handled": True, "kind": "no_slots",
                    "reply": f"I'm afraid {hint['date'].strftime('%A %d %b')} is fully "
                             "booked. Would another day work?"}
        labels = "; ".join(_fmt_slot(s, tz) for s in slots)
        return {"handled": True, "kind": "clarify",
                "reply": f"For {hint['date'].strftime('%A %d %b')} I have: {labels}. "
                         "What time suits you?"}

    start_local = datetime.combine(hint["date"], time(hint["hour"], hint["minute"]),
                                   tzinfo=tz)
    if existing is not None and existing.start_at and existing.end_at:
        duration = int((_aware(existing.end_at) - _aware(existing.start_at))
                       .total_seconds() // 60)
    else:
        duration = settings.default_duration_minutes or 30
    end_local = start_local + timedelta(minutes=duration)
    ok, reason = availability.is_slot_available(
        db, wid, start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        assigned_to=existing.assigned_to if existing else "primary",
        exclude_id=existing.id if existing else None)
    if not ok:
        slots = availability.available_slots(db, wid, start_local=hint["date"],
                                             end_local=hint["date"] + timedelta(days=6),
                                             limit=5)
        labels = "; ".join(_fmt_slot(s, tz) for s in slots) or "none right now"
        return {"handled": True, "kind": "conflict",
                "reply": f"That time doesn't work — {reason}. "
                         f"How about: {labels}?"}

    if existing is not None and existing.status == AppointmentStatus.confirmed:
        try:
            appt_svc.apply_reschedule(
                db, wid, existing, start_local.isoformat(), end_local.isoformat(),
                reason="prospect requested new time", actor_type=ActorType.agent,
                actor_id=agent_id)
            db.commit()
            return {"handled": True, "appointment_id": str(existing.id),
                    "kind": "rescheduled",
                    "reply": f"Done — moved to {start_local.strftime('%a %d %b %I:%M %p')}. "
                             "Calendar updated."}
        except appt_svc.BookingConflict:
            pass  # apply_reschedule validates before mutating

    try:
        appt = appt_svc.create_appointment(
            db, wid, lead_id=lead.id,
            appointment_type=_infer_type(getattr(conv, "last_intent", "") or "demo"),
            start_at=start_local.isoformat(), end_at=end_local.isoformat(),
            conversation_id=conv.id, source="conversation",
            source_agent_id=agent_id,
            status=(AppointmentStatus.awaiting_confirmation
                    if settings.require_confirmation
                    else AppointmentStatus.confirmed),
            check_availability=True, auto_finalize=False)
    except appt_svc.BookingConflict as exc:
        return {"handled": True, "kind": "conflict",
                "reply": f"Sorry, {exc}. Let me suggest alternatives."}
    db.flush()
    if appt.status == AppointmentStatus.confirmed:
        appt_svc.finalize_booking(db, wid, appt, actor_type=ActorType.agent,
                                  actor_id=agent_id)
        db.commit()
        return {"handled": True, "appointment_id": str(appt.id), "kind": "booked",
                "reply": f"Confirmed — '{appt.title}' on "
                         f"{_fmt_when(appt, tz)}. Reminder set."}
    _request_booking_approval(db, wid, appt, agent_id)
    db.commit()
    return {"handled": True, "appointment_id": str(appt.id),
            "kind": "pending_approval",
            "reply": f"I've requested {start_local.strftime('%a %d %b at %I:%M %p')} "
                     "for you — pending quick internal approval. I'll confirm shortly."}


def _offer_reply(appt: Appointment, lead: Lead, tz: ZoneInfo) -> str:
    return (f"I'd be happy to set up a demo for {lead.business_name}. "
            f"Here are the next available times:\n{_proposed_slot_label(appt, tz)}\n"
            "Which one works for you?")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fmt_when(appt: Appointment, tz: ZoneInfo) -> str:
    s = _aware(appt.start_at).astimezone(tz)
    e = _aware(appt.end_at).astimezone(tz)
    return (f"{s.strftime('%A %d %b')}, "
            f"{s.strftime('%I:%M %p').lstrip('0')}–{e.strftime('%I:%M %p').lstrip('0')}")


def _infer_type(text: str) -> AppointmentType:
    t = text.lower()
    if "demo" in t or "demonstration" in t:
        return AppointmentType.product_demo
    if "discovery" in t:
        return AppointmentType.discovery_call
    if "consult" in t or "pricing" in t or "quote" in t:
        return AppointmentType.sales_consultation
    if "follow" in t:
        return AppointmentType.follow_up
    if "onboard" in t:
        return AppointmentType.onboarding
    if "internal" in t or "team" in t:
        return AppointmentType.internal
    return AppointmentType.product_demo

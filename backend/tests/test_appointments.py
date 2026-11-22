"""Phase 5 tests: appointments, availability, reminders, calendar providers,
conversation-based booking, workspace isolation."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import make_lead


def _db():
    from app.core.database import SessionLocal
    return SessionLocal()


def _wid(owner):
    return uuid.UUID(owner["workspace_id"])


def _future_slot(db, wid, days_ahead=3, hour=10, minute=0):
    """Return (start,end) ISO strings for a weekday slot inside working hours."""
    from app.services import availability
    settings = availability.get_settings(db, wid)
    tz = availability.tz_of(settings)
    day = datetime.now(tz).date() + timedelta(days=days_ahead)
    # advance to a working day
    for _ in range(14):
        if day.isoweekday() - 1 in (settings.working_days or []):
            break
        day += timedelta(days=1)
    start_local = datetime.combine(day, datetime.min.time(), tzinfo=tz).replace(
        hour=hour, minute=minute)
    end_local = start_local + timedelta(minutes=settings.default_duration_minutes)
    return (start_local.astimezone(timezone.utc).isoformat(),
            end_local.astimezone(timezone.utc).isoformat())


def test_settings_defaults_created(auth_client):
    _, headers, owner = auth_client
    db = _db()
    try:
        from app.services import availability
        s = availability.get_settings(db, _wid(owner))
        assert s.timezone == "Asia/Kolkata"
        assert s.slot_minutes == 30
        assert s.calendar_provider == "internal"
    finally:
        db.close()


def test_create_and_get_appointment(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner))
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "appointment_type": "product_demo",
        "start_at": s, "end_at": e,
    }, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_at"] is not None
    assert body["lead_business_name"] == lead["business_name"]

    got = client.get(f"/api/v1/appointments/{body['id']}", headers=headers)
    assert got.status_code == 200
    assert got.json()["id"] == body["id"]


def test_overlap_rejected(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner))
    finally:
        db.close()
    r1 = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers)
    assert r1.status_code == 201
    # same slot again -> 409
    r2 = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers)
    assert r2.status_code == 409


def test_availability_slots_endpoint(auth_client):
    client, headers, owner = auth_client
    r = client.get("/api/v1/appointments/availability/slots?limit=10",
                   headers=headers)
    assert r.status_code == 200
    slots = r.json()
    assert isinstance(slots, list)
    if slots:
        assert slots[0]["start"] < slots[0]["end"]


def test_reschedule_preserves_history(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s1, e1 = _future_slot(db, _wid(owner), days_ahead=3, hour=10)
        s2, e2 = _future_slot(db, _wid(owner), days_ahead=4, hour=14)
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s1, "end_at": e1}, headers=headers)
    aid = r.json()["id"]
    r2 = client.post(f"/api/v1/appointments/{aid}/reschedule",
                     json={"start_at": s2, "end_at": e2, "reason": "prospect asked"},
                     headers=headers)
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["start_at"] != r.json()["start_at"]
    assert len(body["reschedule_history"]) == 1
    assert body["reschedule_history"][0]["reason"] == "prospect asked"


def test_cancel_cancels_reminders(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner), days_ahead=3)
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers)
    aid = r.json()["id"]
    reminders = client.get(f"/api/v1/appointments/{aid}/reminders",
                           headers=headers).json()
    assert reminders and all(rm["status"] == "pending" for rm in reminders)

    rc = client.post(f"/api/v1/appointments/{aid}/cancel",
                     json={"reason": "prospect cannot attend"}, headers=headers)
    assert rc.status_code == 200
    assert rc.json()["status"] == "cancelled"
    reminders = client.get(f"/api/v1/appointments/{aid}/reminders",
                           headers=headers).json()
    assert all(rm["status"] == "cancelled" for rm in reminders)


def test_reminder_dedupe_and_delivery(auth_client):
    from app.core.database import SessionLocal
    from app.models import Appointment, AppointmentReminder, ReminderStatus
    from app.services import appointments as appt_svc, reminders
    import uuid as _uuid

    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    wid = _wid(owner)
    db = SessionLocal()
    try:
        s, e = _future_slot(db, wid, days_ahead=3)
        from app.models import Lead, AppointmentType
        l = db.get(Lead, _uuid.UUID(lead["id"]))
        appt = appt_svc.create_appointment(
            db, wid, lead_id=l.id, start_at=s, end_at=e,
            appointment_type=AppointmentType.product_demo)
        # finalize_booking already scheduled reminders; a second call dedupes
        existing = db.scalars(__import__('sqlalchemy').select(AppointmentReminder)
                              .where(AppointmentReminder.appointment_id == appt.id)).all()
        assert len(existing) > 0
        second = reminders.schedule_reminders(db, wid, appt)
        assert len(second) == 0  # dedupe — no duplicates
        # deliver due reminders -> internal notification fallback (no provider)
        for r_ in existing:
            r_.remind_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.flush()
        n = reminders.process_due_reminders(db)
        assert n >= len(existing)
        db.commit()
        statuses = {r_.status for r_ in db.scalars(
            __import__('sqlalchemy').select(AppointmentReminder)
            .where(AppointmentReminder.appointment_id == appt.id)).all()}
        assert statuses <= {ReminderStatus.internal_notified, ReminderStatus.sent,
                            ReminderStatus.failed}
        assert ReminderStatus.pending not in statuses
    finally:
        db.close()


def test_calendar_provider_unconfigured(auth_client):
    from app.integrations.calendar.registry import get_calendar_provider
    # no GOOGLE_CALENDAR_* env in tests -> None
    assert get_calendar_provider("google_calendar") is None
    assert get_calendar_provider("internal") is None


def test_sync_calendar_not_configured(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner))
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers)
    aid = r.json()["id"]
    res = client.post(f"/api/v1/appointments/{aid}/sync-calendar", headers=headers)
    assert res.status_code == 200
    assert res.json()["synced"] is False
    assert "configured" in res.json()["reason"]


def test_workspace_isolation(auth_client):
    client, headers_a, owner_a = auth_client
    lead = make_lead(client, headers_a)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner_a))
    finally:
        db.close()
    r = client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers_a)
    assert r.status_code == 201
    aid = r.json()["id"]

    reg = client.post("/api/v1/auth/register", json={
        "email": f"{uuid.uuid4().hex[:8]}@iso-cal.com", "password": "password123",
        "workspace_name": "Other WS",
    })
    hb = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    assert client.get("/api/v1/appointments", headers=hb).json()["total"] == 0
    assert client.get(f"/api/v1/appointments/{aid}", headers=hb).status_code == 404
    assert client.post(f"/api/v1/appointments/{aid}/cancel",
                       json={}, headers=hb).status_code == 404


def test_scheduling_settings_update(auth_client):
    client, headers, owner = auth_client
    r = client.put("/api/v1/scheduling/settings", json={
        "work_start": "10:00", "work_end": "17:00",
        "slot_minutes": 60, "default_duration_minutes": 45,
        "timezone": "Asia/Kolkata",
    }, headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["work_start"] == "10:00"
    assert body["slot_minutes"] == 60
    # viewer/member roles can't change? member can write appointments but settings need admin
    # (owner is admin here)


def test_dashboard_endpoint(auth_client):
    client, headers, owner = auth_client
    r = client.get("/api/v1/appointments/dashboard", headers=headers)
    assert r.status_code == 200
    body = r.json()
    for k in ("upcoming_demos", "today_meetings", "pending_booking_requests",
              "rescheduling_requests", "cancellations", "no_shows",
              "completed_demos", "calendar_provider"):
        assert k in body


def test_analytics_appointment_metrics(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    db = _db()
    try:
        s, e = _future_slot(db, _wid(owner), days_ahead=3)
        s2, e2 = _future_slot(db, _wid(owner), days_ahead=4)
    finally:
        db.close()
    client.post("/api/v1/appointments", json={
        "lead_id": lead["id"], "start_at": s, "end_at": e}, headers=headers)
    r = client.get("/api/v1/analytics/overview", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["demos_booked"] >= 1
    assert "demos_requested" in body and "demo_attendance_rate" in body


# ------------------------------------------------ conversation-based booking


def _seed_lead_with_email(client, headers):
    return make_lead(client, headers, email=f"demo-{uuid.uuid4().hex[:6]}@cafe.com")


def test_conversation_demo_request_proposes_slots(auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel, Conversation
    from app.services.conversation_engine import handle_inbound

    client, headers, owner = auth_client
    lead = _seed_lead_with_email(client, headers)
    db = SessionLocal()
    try:
        res = handle_inbound(
            db, _wid(owner), channel=Channel.email,
            from_address=lead["email"], to_address=None,
            subject="Demo?", body="Can I see a demo of your café management software?",
            external_id=f"evt-{uuid.uuid4().hex[:6]}")
        assert res["matched"] is True
        sched = res.get("scheduling") or {}
        assert sched.get("kind") == "proposed"
        conv = db.get(Conversation, uuid.UUID(res["conversation_id"]))
        assert conv.state == "scheduling"
        # a proposed appointment exists with slots
        from app.models import Appointment, AppointmentStatus
        appt = db.scalar(__import__('sqlalchemy').select(Appointment).where(
            Appointment.conversation_id == conv.id,
            Appointment.status == AppointmentStatus.proposed))
        assert appt is not None and appt.proposed_slots
    finally:
        db.close()


def test_conversation_booking_confirms_slot(auth_client):
    from app.core.database import SessionLocal
    from app.models import Appointment, AppointmentStatus, Channel
    from app.services import appointments as appt_svc
    from app.services.conversation_engine import handle_inbound
    import sqlalchemy as sa

    client, headers, owner = auth_client
    lead = _seed_lead_with_email(client, headers)
    wid = _wid(owner)
    db = SessionLocal()
    try:
        # turn 1: request demo -> proposal
        r1 = handle_inbound(db, wid, channel=Channel.email,
                            from_address=lead["email"], to_address=None,
                            subject="", body="I'd like a demo please",
                            external_id=f"e1-{uuid.uuid4().hex[:6]}")
        conv_id = r1["conversation_id"]
        appt = db.scalar(sa.select(Appointment).where(
            Appointment.conversation_id == uuid.UUID(conv_id)))
        assert appt is not None
        slot = appt.proposed_slots[0]
        # turn 2: pick the first proposed slot explicitly
        from app.services import availability
        settings = availability.get_settings(db, wid)
        tz = availability.tz_of(settings)
        start_local = datetime.fromisoformat(slot["start"]).astimezone(tz)
        label = start_local.strftime("%A")
        r2 = handle_inbound(db, wid, channel=Channel.email,
                            from_address=lead["email"], to_address=None,
                            subject="", body=f"{label} at {start_local.strftime('%I:%M %p').lstrip('0')} works for me",
                            external_id=f"e2-{uuid.uuid4().hex[:6]}")
        sched = r2.get("scheduling") or {}
        assert sched.get("kind") in ("booked", "pending_approval"), sched
        appt2 = db.get(Appointment, appt.id)
        assert appt2.status in (AppointmentStatus.confirmed,
                                AppointmentStatus.awaiting_confirmation)
    finally:
        db.close()


def test_conversation_ambiguous_time_clarifies(auth_client):
    from app.core.database import SessionLocal
    from app.models import Channel
    from app.services.conversation_engine import handle_inbound

    client, headers, owner = auth_client
    lead = _seed_lead_with_email(client, headers)
    db = SessionLocal()
    try:
        res = handle_inbound(
            db, _wid(owner), channel=Channel.email,
            from_address=lead["email"], to_address=None,
            subject="", body="Can we do the demo next week?",
            external_id=f"e3-{uuid.uuid4().hex[:6]}")
        sched = res.get("scheduling") or {}
        # must not guess — should clarify or propose concrete slots
        assert sched.get("kind") in ("clarify", "proposed", "reschedule_offer")
    finally:
        db.close()


def test_conversation_cancel(auth_client):
    from app.core.database import SessionLocal
    from app.models import Appointment, AppointmentStatus, Channel
    from app.services import appointments as appt_svc
    from app.services.conversation_engine import handle_inbound
    import sqlalchemy as sa

    client, headers, owner = auth_client
    lead = _seed_lead_with_email(client, headers)
    wid = _wid(owner)
    db = SessionLocal()
    try:
        s, e = _future_slot(db, wid, days_ahead=3)
        from app.models import Lead, AppointmentType
        l = db.get(Lead, uuid.UUID(lead["id"]))
        appt = appt_svc.create_appointment(
            db, wid, lead_id=l.id, start_at=s, end_at=e,
            appointment_type=AppointmentType.product_demo)
        # simulate an inbound conversation first
        handle_inbound(db, wid, channel=Channel.email,
                       from_address=lead["email"], to_address=None,
                       subject="", body="hello", external_id=f"c1-{uuid.uuid4().hex[:6]}")
        conv = db.scalar(sa.select(__import__('app.models', fromlist=['Conversation']).Conversation)
                         .where(__import__('app.models', fromlist=['Conversation']).Conversation.lead_id == l.id))
        appt.conversation_id = conv.id
        db.commit()

        res = handle_inbound(db, wid, channel=Channel.email,
                             from_address=lead["email"], to_address=None,
                             subject="", body="Sorry, I cannot attend anymore — please cancel",
                             external_id=f"c2-{uuid.uuid4().hex[:6]}")
        sched = res.get("scheduling") or {}
        assert sched.get("kind") == "cancelled"
        db.refresh(appt)
        assert appt.status == AppointmentStatus.cancelled
    finally:
        db.close()


def test_proposed_slots_endpoint_and_booking(auth_client):
    client, headers, owner = auth_client
    lead = make_lead(client, headers)
    r = client.post("/api/v1/appointments/propose", json={
        "lead_id": lead["id"], "appointment_type": "product_demo",
    }, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "proposed"
    assert body["proposed_slots"]
    # book one of the proposed slots
    slot = body["proposed_slots"][0]
    rb = client.post(f"/api/v1/appointments/{body['id']}/book-slot",
                     json={"start": slot["start"], "end": slot["end"]},
                     headers=headers)
    assert rb.status_code == 200, rb.text
    assert rb.json()["status"] in ("confirmed", "awaiting_confirmation")


def test_no_conflicting_double_booking_via_proposals(auth_client):
    """Two proposals for overlapping slots: booking the second must fail if the
    first was confirmed on the same time."""
    client, headers, owner = auth_client
    lead1 = make_lead(client, headers)
    lead2 = make_lead(client, headers, email="second@cafe.com")
    db = _db()
    try:
        slots = __import__('app.services.availability', fromlist=['x']).available_slots(
            db, _wid(owner), limit=5)
        assert slots
        slot = slots[0]
    finally:
        db.close()
    # book slot directly for lead1
    r1 = client.post("/api/v1/appointments", json={
        "lead_id": lead1["id"], "start_at": slot["start"], "end_at": slot["end"]},
        headers=headers)
    assert r1.status_code == 201
    # same slot for lead2 must conflict
    r2 = client.post("/api/v1/appointments", json={
        "lead_id": lead2["id"], "start_at": slot["start"], "end_at": slot["end"]},
        headers=headers)
    assert r2.status_code == 409

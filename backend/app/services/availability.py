"""Internal availability engine — works without any external calendar.

All math is done in the workspace timezone (default Asia/Kolkata) and stored
as UTC. Configurable: working days, hours, lunch, slot grid, buffers, notice,
horizon, holidays and blackout windows.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Appointment,
    AppointmentStatus,
    SchedulingSettings,
)

# Statuses that occupy a representative's calendar.
BLOCKING_STATUSES = {
    AppointmentStatus.proposed,
    AppointmentStatus.awaiting_confirmation,
    AppointmentStatus.confirmed,
    AppointmentStatus.rescheduling_requested,
}

DEFAULT_SETTINGS = dict(
    timezone="Asia/Kolkata",
    working_days=[0, 1, 2, 3, 4],
    work_start="09:00",
    work_end="18:00",
    lunch_start=None,
    lunch_end=None,
    slot_minutes=30,
    buffer_minutes=15,
    min_notice_hours=2,
    max_horizon_days=30,
    holidays=[],
    blackout_windows=[],
    reminder_offsets_minutes=[1440, 60],
    default_duration_minutes=30,
    require_confirmation=True,
    allow_autonomous_booking=True,
    calendar_provider="internal",
)


def get_settings(db: Session, wid: uuid.UUID) -> SchedulingSettings:
    row = db.scalar(select(SchedulingSettings).where(SchedulingSettings.workspace_id == wid))
    if row is None:
        row = SchedulingSettings(workspace_id=wid, **DEFAULT_SETTINGS)
        db.add(row)
        db.flush()
    return row


def update_settings(db: Session, wid: uuid.UUID, data: dict) -> SchedulingSettings:
    row = get_settings(db, wid)
    allowed = set(DEFAULT_SETTINGS) - {"timezone"} | {"timezone"}
    for key, value in data.items():
        if key in allowed and hasattr(row, key):
            setattr(row, key, value)
    db.flush()
    return row


def tz_of(settings: SchedulingSettings) -> ZoneInfo:
    try:
        return ZoneInfo(settings.timezone or "Asia/Kolkata")
    except Exception:
        return ZoneInfo("Asia/Kolkata")


def _parse_hhmm(value: str | None) -> time | None:
    if not value:
        return None
    try:
        h, m = value.split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def _parse_date(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _blackout_windows_utc(settings: SchedulingSettings, tz: ZoneInfo,
                          day: date) -> list[tuple[datetime, datetime]]:
    """Return blackout intervals (UTC) covering `day` in the workspace tz."""
    out: list[tuple[datetime, datetime]] = []
    for bw in settings.blackout_windows or []:
        if not isinstance(bw, dict):
            continue
        s = _parse_hhmm(bw.get("start"))
        e = _parse_hhmm(bw.get("end"))
        if not s or not e:
            continue
        # Blackouts apply every working day unless an ISO date is given.
        only = _parse_date(bw.get("date"))
        if only is not None and only != day:
            continue
        start_local = datetime.combine(day, s, tzinfo=tz)
        end_local = datetime.combine(day, e, tzinfo=tz)
        if end_local <= start_local:
            end_local += timedelta(days=1)
        out.append((start_local.astimezone(timezone.utc),
                    end_local.astimezone(timezone.utc)))
    return out


def _day_windows(settings: SchedulingSettings, tz: ZoneInfo,
                 day: date) -> list[tuple[datetime, datetime]]:
    """Free working windows (UTC) for a single local date, minus lunch/breaks."""
    wd = day.isoweekday() - 1  # Monday=0
    if wd not in (settings.working_days or []):
        return []
    if day.isoformat() in (settings.holidays or []):
        return []
    ws = _parse_hhmm(settings.work_start)
    we = _parse_hhmm(settings.work_end)
    if ws is None or we is None:
        return []
    start_local = datetime.combine(day, ws, tzinfo=tz)
    end_local = datetime.combine(day, we, tzinfo=tz)
    if end_local <= start_local:
        end_local += timedelta(days=1)

    windows = [(start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc))]

    ls = _parse_hhmm(settings.lunch_start)
    le = _parse_hhmm(settings.lunch_end)
    if ls and le:
        lunch_s = datetime.combine(day, ls, tzinfo=tz).astimezone(timezone.utc)
        lunch_e = datetime.combine(day, le, tzinfo=tz).astimezone(timezone.utc)
        windows = _subtract(windows, lunch_s, lunch_e)

    for bs, be in _blackout_windows_utc(settings, tz, day):
        windows = _subtract(windows, bs, be)
    return windows


def _subtract(windows: list[tuple[datetime, datetime]],
              cut_s: datetime, cut_e: datetime) -> list[tuple[datetime, datetime]]:
    out = []
    for s, e in windows:
        if cut_e <= s or cut_s >= e:
            out.append((s, e))
            continue
        if cut_s > s:
            out.append((s, min(cut_s, e)))
        if cut_e < e:
            out.append((max(cut_e, s), e))
    return [(s, e) for s, e in out if e > s]


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalize to aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def busy_intervals(db: Session, wid: uuid.UUID, assigned_to: str,
                   start_utc: datetime, end_utc: datetime,
                   exclude_id: uuid.UUID | None = None) -> list[tuple[datetime, datetime]]:
    """Existing bookings (plus buffer) that block `assigned_to`."""
    q = select(Appointment).where(
        Appointment.workspace_id == wid,
        Appointment.status.in_(BLOCKING_STATUSES),
        Appointment.start_at < end_utc,
        Appointment.end_at > start_utc,
    )
    if assigned_to:
        q = q.where(Appointment.assigned_to == assigned_to)
    rows = db.scalars(q).all()
    out = []
    for a in rows:
        if exclude_id and a.id == exclude_id:
            continue
        out.append((_aware(a.start_at), _aware(a.end_at)))
    return out


def _free_within(windows: list[tuple[datetime, datetime]],
                 busy: list[tuple[datetime, datetime]],
                 buffer: timedelta) -> list[tuple[datetime, datetime]]:
    """Subtract busy intervals (expanded by buffer on both sides)."""
    free = list(windows)
    for bs, be in busy:
        free = _subtract(free, bs - buffer, be + buffer)
    return free


def available_slots(db: Session, wid: uuid.UUID,
                    start_local: date | None = None,
                    end_local: date | None = None,
                    duration_minutes: int | None = None,
                    assigned_to: str = "primary",
                    limit: int = 60) -> list[dict]:
    """Compute bookable start/end pairs (UTC) within the horizon."""
    settings = get_settings(db, wid)
    tz = tz_of(settings)
    now_utc = datetime.now(timezone.utc)
    earliest = now_utc + timedelta(hours=settings.min_notice_hours or 0)
    horizon = now_utc + timedelta(days=settings.max_horizon_days or 30)

    if start_local is None:
        start_local = datetime.now(tz).date()
    if end_local is None:
        end_local = start_local + timedelta(days=settings.max_horizon_days or 30)

    duration = duration_minutes or settings.default_duration_minutes or 30
    grid = max(5, settings.slot_minutes or 30)
    buffer = timedelta(minutes=settings.buffer_minutes or 0)
    slot_len = timedelta(minutes=duration)

    slots: list[dict] = []
    day = start_local
    while day <= end_local and len(slots) < limit:
        for ws, we in _day_windows(settings, tz, day):
            free = _free_within(
                [(ws, we)],
                busy_intervals(db, wid, assigned_to, ws - slot_len, we + slot_len),
                buffer,
            )
            for fs, fe in free:
                # align slot starts to the configured grid in local time
                cursor = fs
                while cursor + slot_len <= fe and len(slots) < limit:
                    candidate_end = cursor + slot_len
                    if cursor >= earliest and cursor + slot_len <= horizon:
                        # verify this exact slot isn't inside a busy/buffer zone
                        conflicts = [
                            (bs - buffer, be + buffer)
                            for bs, be in busy_intervals(db, wid, assigned_to,
                                                         cursor, candidate_end)
                        ]
                        ok = all(candidate_end <= cs or cursor >= ce for cs, ce in conflicts)
                        if ok:
                            slots.append({
                                "start": cursor.isoformat(),
                                "end": candidate_end.isoformat(),
                                "start_local": cursor.astimezone(tz).isoformat(),
                                "end_local": candidate_end.astimezone(tz).isoformat(),
                            })
                    # advance by grid steps aligned to local wall-clock grid
                    local_cursor = cursor.astimezone(tz)
                    mins = local_cursor.hour * 60 + local_cursor.minute
                    next_min = ((mins // grid) + 1) * grid
                    cursor = local_cursor.replace(
                        hour=next_min // 60, minute=next_min % 60,
                        second=0, microsecond=0,
                    ).astimezone(timezone.utc)
        day += timedelta(days=1)
    return slots[:limit]


def is_slot_available(db: Session, wid: uuid.UUID, start_utc: datetime,
                      end_utc: datetime, assigned_to: str = "primary",
                      exclude_id: uuid.UUID | None = None) -> tuple[bool, str | None]:
    """Validate a concrete booking against working hours and conflicts."""
    start_utc, end_utc = _aware(start_utc), _aware(end_utc)
    if end_utc <= start_utc:
        return False, "end time must be after start time"
    settings = get_settings(db, wid)
    tz = tz_of(settings)
    local_start = start_utc.astimezone(tz)
    local_end = end_utc.astimezone(tz)

    if local_start.date() != local_end.date():
        return False, "appointment cannot span multiple days"

    day_windows = _day_windows(settings, tz, local_start.date())
    if not day_windows:
        return False, "date is outside working days/holidays/blackouts"
    inside = any(s <= start_utc and end_utc <= e for s, e in day_windows)
    if not inside:
        return False, "time is outside working hours or a break/blackout"

    if start_utc < datetime.now(timezone.utc) + timedelta(hours=settings.min_notice_hours or 0):
        return False, "start time is inside the minimum advance booking notice"
    if start_utc > datetime.now(timezone.utc) + timedelta(days=settings.max_horizon_days or 30):
        return False, "start time is beyond the maximum booking horizon"

    buffer = timedelta(minutes=settings.buffer_minutes or 0)
    for bs, be in busy_intervals(db, wid, assigned_to, start_utc - buffer,
                                 end_utc + buffer, exclude_id=exclude_id):
        if start_utc - buffer < be and end_utc + buffer > bs:
            return False, "conflicts with an existing appointment"
    return True, None

"""Request/response schemas for calendar provider operations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CreateEventRequest:
    summary: str
    start: datetime
    end: datetime
    description: str = ""
    location: str | None = None
    attendees: list[str] = field(default_factory=list)
    timezone: str = "UTC"
    idempotency_key: str | None = None


@dataclass
class UpdateEventRequest:
    event_id: str
    summary: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None


@dataclass
class EventStatus:
    event_id: str
    status: str            # confirmed | cancelled | tentative | unknown
    start: datetime | None = None
    end: datetime | None = None
    html_link: str | None = None

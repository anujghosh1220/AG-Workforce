"""Calendar provider interfaces.

Providers must never fabricate events: a calendar event is only reported as
created/updated when the provider API confirms success. When no provider is
configured the internal scheduling engine remains fully functional.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import httpx


class CalendarNotConfigured(Exception):
    """Raised when a calendar provider is invoked without credentials."""


class CalendarProviderError(Exception):
    """Raised when a configured provider fails."""


@dataclass
class CalendarEvent:
    """Provider-neutral calendar event payload."""
    summary: str
    start: datetime          # timezone-aware
    end: datetime            # timezone-aware
    description: str = ""
    location: str | None = None
    attendees: list[str] = field(default_factory=list)
    timezone: str = "UTC"


@dataclass
class CalendarEventResult:
    event_id: str
    status: str = "confirmed"   # confirmed | cancelled
    html_link: str | None = None
    raw: dict | None = None


@dataclass
class AvailabilityWindow:
    start: datetime
    end: datetime


class CalendarProvider(Protocol):
    name: str

    def configured(self) -> bool: ...
    def check_availability(self, start: datetime, end: datetime) -> list[AvailabilityWindow]: ...
    def create_event(self, event: CalendarEvent, idempotency_key: str | None = None) -> CalendarEventResult: ...
    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEventResult: ...
    def cancel_event(self, event_id: str) -> CalendarEventResult: ...
    def get_event(self, event_id: str) -> CalendarEventResult | None: ...
    def sync_event_status(self, event_id: str) -> str: ...  # confirmed|cancelled|unknown


def http_client(timeout: float = 20.0) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=True)


def env(name: str) -> str | None:
    return os.environ.get(name) or None

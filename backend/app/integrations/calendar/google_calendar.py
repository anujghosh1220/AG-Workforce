"""Google Calendar adapter (OAuth 2.0 refresh-token flow).

Configured entirely via environment — no credentials are required for local
development. Without GOOGLE_CALENDAR_CLIENT_ID/SECRET/REFRESH_TOKEN the
provider reports configured() == False and the internal scheduling engine
keeps working.

Duplicate prevention: before creating an event we look up an existing event by
our idempotency key stored in a private extended property, and callers persist
the returned event id on the appointment so retries update rather than
duplicate.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

from .base import (
    AvailabilityWindow,
    CalendarEvent,
    CalendarEventResult,
    CalendarNotConfigured,
    CalendarProviderError,
    env,
    http_client,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
IDEMPOTENCY_PROP = "agwIdempotencyKey"


def _require() -> dict:
    cfg = {
        "client_id": env("GOOGLE_CALENDAR_CLIENT_ID"),
        "client_secret": env("GOOGLE_CALENDAR_CLIENT_SECRET"),
        "refresh_token": env("GOOGLE_CALENDAR_REFRESH_TOKEN"),
        "calendar_id": env("GOOGLE_CALENDAR_ID") or "primary",
    }
    if not all([cfg["client_id"], cfg["client_secret"], cfg["refresh_token"]]):
        raise CalendarNotConfigured(
            "Google Calendar requires GOOGLE_CALENDAR_CLIENT_ID, "
            "GOOGLE_CALENDAR_CLIENT_SECRET and GOOGLE_CALENDAR_REFRESH_TOKEN"
        )
    return cfg


class GoogleCalendarProvider:
    name = "google_calendar"

    def __init__(self):
        self._access_token: str | None = None
        self._token_expiry: float = 0.0

    # ------------------------------------------------------------ auth

    def configured(self) -> bool:
        return all([
            env("GOOGLE_CALENDAR_CLIENT_ID"),
            env("GOOGLE_CALENDAR_CLIENT_SECRET"),
            env("GOOGLE_CALENDAR_REFRESH_TOKEN"),
        ])

    def _token(self) -> str:
        if self._access_token and time.time() < self._token_expiry - 60:
            return self._access_token
        cfg = _require()
        try:
            with http_client(20) as client:
                r = client.post(TOKEN_URL, data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "refresh_token": cfg["refresh_token"],
                    "grant_type": "refresh_token",
                })
            if r.status_code >= 400:
                raise CalendarProviderError(f"Google token refresh failed: {r.status_code}")
            data = r.json()
        except CalendarProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CalendarProviderError(f"Google token refresh failed: {exc}") from exc
        self._access_token = data["access_token"]
        self._token_expiry = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _request(self, method: str, path: str, **kwargs):
        cfg = _require()
        token = self._token()
        url = f"{CALENDAR_API}/calendars/{cfg['calendar_id']}{path}"
        try:
            with http_client(30) as client:
                r = client.request(method, url, headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise CalendarProviderError(f"Google Calendar request failed: {exc}") from exc
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise CalendarProviderError(
                f"Google Calendar {method} {path} failed: {r.status_code} {r.text[:300]}"
            )
        return r.json() if r.content else {}

    # ------------------------------------------------------------ payloads

    @staticmethod
    def _event_body(event: CalendarEvent, idempotency_key: str | None = None) -> dict:
        body = {
            "summary": event.summary,
            "description": event.description,
            "start": {"dateTime": event.start.isoformat(), "timeZone": event.timezone},
            "end": {"dateTime": event.end.isoformat(), "timeZone": event.timezone},
        }
        if event.location:
            body["location"] = event.location
        if event.attendees:
            body["attendees"] = [{"email": a} for a in event.attendees]
        if idempotency_key:
            body["extendedProperties"] = {
                "private": {IDEMPOTENCY_PROP: idempotency_key}
            }
        return body

    @staticmethod
    def _result(data: dict) -> CalendarEventResult:
        status = "cancelled" if data.get("status") == "cancelled" else "confirmed"
        return CalendarEventResult(
            event_id=data.get("id", ""),
            status=status,
            html_link=data.get("htmlLink"),
            raw=data,
        )

    # ------------------------------------------------------------ API

    def check_availability(self, start: datetime, end: datetime) -> list[AvailabilityWindow]:
        """Return free windows inside [start, end] using Google freebusy."""
        cfg = _require()
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(),
                "items": [{"id": cfg["calendar_id"]}]}
        data = self._request("POST", "/freeBusy", json=body)
        if not data:
            return []
        busy = (data.get("calendars", {}).get(cfg["calendar_id"], {})
                .get("busy", []))
        windows: list[AvailabilityWindow] = []
        cursor = start
        for b in sorted(busy, key=lambda x: x.get("start", "")):
            bs = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
            be = datetime.fromisoformat(b["end"].replace("Z", "+00:00"))
            if bs > cursor:
                windows.append(AvailabilityWindow(cursor, min(bs, end)))
            cursor = max(cursor, be)
        if cursor < end:
            windows.append(AvailabilityWindow(cursor, end))
        return [w for w in windows if w.end > w.start]

    def _find_by_idempotency_key(self, key: str) -> str | None:
        data = self._request("GET", "/events", params={
            "privateExtendedProperty": f"{IDEMPOTENCY_PROP}={key}",
            "maxResults": 1,
            "singleEvents": "true",
        })
        items = (data or {}).get("items") or []
        return items[0]["id"] if items else None

    def create_event(self, event: CalendarEvent, idempotency_key: str | None = None) -> CalendarEventResult:
        cfg = _require()
        if idempotency_key:
            existing = self._find_by_idempotency_key(idempotency_key)
            if existing:
                data = self._request("GET", f"/events/{existing}")
                if data:
                    return self._result(data)
        body = self._event_body(event, idempotency_key)
        if event.attendees:
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": idempotency_key or f"agw-{int(time.time())}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        data = self._request(
            "POST", "/events",
            params={"conferenceDataVersion": 1} if event.attendees else {},
            json=body,
        )
        if not data:
            raise CalendarProviderError("Google Calendar did not return an event")
        return self._result(data)

    def update_event(self, event_id: str, event: CalendarEvent) -> CalendarEventResult:
        cfg = _require()
        body = self._event_body(event)
        data = self._request("PUT", f"/events/{event_id}", json=body)
        if not data:
            raise CalendarProviderError(f"Google Calendar event {event_id} not found")
        return self._result(data)

    def cancel_event(self, event_id: str) -> CalendarEventResult:
        _require()
        data = self._request("POST", f"/events/{event_id}/cancel")
        if not data:
            # Fall back: event may not exist — report honestly.
            raise CalendarProviderError(f"Google Calendar event {event_id} not found")
        return self._result(data)

    def get_event(self, event_id: str) -> CalendarEventResult | None:
        _require()
        data = self._request("GET", f"/events/{event_id}")
        return self._result(data) if data else None

    def sync_event_status(self, event_id: str) -> str:
        res = self.get_event(event_id)
        if res is None:
            return "unknown"
        return res.status

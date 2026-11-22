"""Email provider interface. Implementations must never fabricate sends."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class OutboundEmail:
    to: str
    subject: str
    body: str
    from_address: str | None = None
    reply_to: str | None = None
    headers: dict | None = None  # e.g. {"In-Reply-To": ..., "References": ...}


@dataclass
class SentEmail:
    provider_message_id: str
    to: str
    status: str = "sent"


@dataclass
class EmailEvent:
    """Normalized inbound/delivery event parsed from a provider webhook."""
    event_id: str
    event_type: str  # received | delivered | bounced | complained | failed
    message_id: str | None = None
    from_address: str | None = None
    to_address: str | None = None
    subject: str | None = None
    body_text: str | None = None
    thread_id: str | None = None
    raw: dict | None = None


class EmailProvider(Protocol):
    name: str
    def configured(self) -> bool: ...
    def send(self, email: OutboundEmail) -> SentEmail: ...
    def verify_webhook(self, headers: dict, raw_body: bytes) -> bool: ...
    def parse_event(self, payload: dict) -> EmailEvent | None: ...


def get_email_provider() -> EmailProvider | None:
    name = os.environ.get("EMAIL_PROVIDER", "resend").lower()
    if name == "resend":
        from app.integrations.email.resend import ResendProvider
        p = ResendProvider()
        return p if p.configured() else None
    return None

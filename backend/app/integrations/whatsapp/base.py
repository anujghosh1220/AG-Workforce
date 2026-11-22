"""WhatsApp provider interface — never fabricates sends."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass
class WhatsAppMessage:
    to: str                      # phone in international format, digits only
    body: str
    template_name: str | None = None
    template_language: str = "en"
    template_components: list | None = None


@dataclass
class WhatsAppResult:
    provider_message_id: str
    status: str = "sent"


@dataclass
class WAEvent:
    event_id: str
    kind: str            # message | status | template_status | unknown
    from_phone: str | None = None
    to_phone: str | None = None
    message_id: str | None = None
    text: str | None = None
    timestamp: int | None = None
    status: str | None = None   # sent|delivered|read|failed
    error: str | None = None
    raw: dict | None = None


class WhatsAppProvider(Protocol):
    name: str
    def configured(self) -> bool: ...
    def send_text(self, msg: WhatsAppMessage) -> WhatsAppResult: ...
    def send_template(self, msg: WhatsAppMessage) -> WhatsAppResult: ...
    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool: ...
    def parse_events(self, payload: dict) -> list[WAEvent]: ...


def get_whatsapp_provider():
    from app.integrations.whatsapp.meta_cloud import MetaCloudProvider
    p = MetaCloudProvider()
    return p if p.configured() else None

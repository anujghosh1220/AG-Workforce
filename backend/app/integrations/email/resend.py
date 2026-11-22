"""Resend email provider — real API + svix webhook verification."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import httpx

from .base import EmailEvent, OutboundEmail, SentEmail

RESEND_URL = "https://api.resend.com/emails"


class ResendProvider:
    name = "resend"

    def _key(self) -> str | None:
        return os.environ.get("RESEND_API_KEY")

    def _from(self) -> str:
        return os.environ.get("EMAIL_FROM", "AG Workforce <onboarding@resend.dev>")

    def configured(self) -> bool:
        return bool(self._key())

    def send(self, email: OutboundEmail) -> SentEmail:
        key = self._key()
        if not key:
            raise RuntimeError("RESEND_API_KEY not configured")
        payload = {
            "from": email.from_address or self._from(),
            "to": [email.to],
            "subject": email.subject,
            "text": email.body,
        }
        if email.reply_to:
            payload["reply_to"] = email.reply_to
        if email.headers:
            payload["headers"] = email.headers
        try:
            r = httpx.post(RESEND_URL, json=payload,
                           headers={"Authorization": f"Bearer {key}"}, timeout=20)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Resend request failed: {exc}") from exc
        if r.status_code >= 400:
            raise RuntimeError(f"Resend error {r.status_code}: {r.text[:300]}")
        data = r.json()
        return SentEmail(provider_message_id=data.get("id", ""), to=email.to)

    # -------------------------------------------------- inbound webhooks (svix)

    def verify_webhook(self, headers: dict, raw_body: bytes) -> bool:
        secret = os.environ.get("RESEND_WEBHOOK_SECRET")
        if not secret:
            return False
        svix_id = headers.get("svix-id") or headers.get("Svix-Id")
        svix_ts = headers.get("svix-timestamp") or headers.get("Svix-Timestamp")
        svix_sig = headers.get("svix-signature") or headers.get("Svix-Signature")
        if not (svix_id and svix_ts and svix_sig):
            return False
        try:
            if abs(time.time() - int(svix_ts)) > 300:
                return False
        except ValueError:
            return False
        secret_bytes = base64.b64decode(secret.removeprefix("whsec_")) if secret.startswith("whsec_") else secret.encode()
        signed = f"{svix_id}.{svix_ts}.{raw_body.decode()}".encode()
        expected = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
        # signature header may contain multiple "v1,<sig>" entries
        for part in svix_sig.split(" "):
            if part.startswith("v1,") and hmac.compare_digest(part[3:], expected):
                return True
        return False

    def parse_event(self, payload: dict) -> EmailEvent | None:
        etype = payload.get("type", "")
        data = payload.get("data") or {}
        mapping = {
            "email.received": "received",
            "email.sent": "delivered",
            "email.delivered": "delivered",
            "email.bounced": "bounced",
            "email.complained": "complained",
            "email.failed": "failed",
        }
        norm = mapping.get(etype)
        if norm is None:
            return None
        # inbound body may be in data.text / data.html or need a follow-up fetch
        body = data.get("text") or ""
        if not body and data.get("html"):
            import re
            body = re.sub(r"<[^>]+>", " ", data["html"])
        return EmailEvent(
            event_id=payload.get("id") or data.get("email_id") or "",
            event_type=norm,
            message_id=data.get("email_id"),
            from_address=(data.get("from") or ""),
            to_address=(data.get("to") or [None])[0] if isinstance(data.get("to"), list) else data.get("to"),
            subject=data.get("subject"),
            body_text=body.strip() or None,
            thread_id=data.get("message_id") or data.get("email_id"),
            raw=payload,
        )

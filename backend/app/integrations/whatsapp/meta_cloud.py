"""Meta WhatsApp Cloud API adapter (Graph API)."""
from __future__ import annotations

import hashlib
import hmac
import os
import re

import httpx

from .base import WAEvent, WhatsAppMessage, WhatsAppResult

GRAPH = "https://graph.facebook.com/{version}/{phone_id}/messages"


class MetaCloudProvider:
    name = "meta_cloud"

    def _cfg(self):
        return {
            "token": os.environ.get("WHATSAPP_ACCESS_TOKEN"),
            "phone_id": os.environ.get("WHATSAPP_PHONE_NUMBER_ID"),
            "waba_id": os.environ.get("WHATSAPP_BUSINESS_ACCOUNT_ID"),
            "app_secret": os.environ.get("WHATSAPP_APP_SECRET"),
            "verify_token": os.environ.get("WHATSAPP_VERIFY_TOKEN"),
            "version": os.environ.get("WHATSAPP_API_VERSION", "v21.0"),
        }

    def configured(self) -> bool:
        c = self._cfg()
        return bool(c["token"] and c["phone_id"])

    def _send(self, payload: dict) -> WhatsAppResult:
        c = self._cfg()
        if not self.configured():
            raise RuntimeError("WhatsApp provider not configured")
        url = GRAPH.format(version=c["version"], phone_id=c["phone_id"])
        try:
            r = httpx.post(url, json=payload,
                           headers={"Authorization": f"Bearer {c['token']}"}, timeout=20)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"WhatsApp API request failed: {exc}") from exc
        if r.status_code >= 400:
            raise RuntimeError(f"WhatsApp API error {r.status_code}: {r.text[:300]}")
        data = r.json().get("messages", [{}])[0]
        return WhatsAppResult(provider_message_id=data.get("id", ""))

    def send_text(self, msg: WhatsAppMessage) -> WhatsAppResult:
        return self._send({
            "messaging_product": "whatsapp",
            "to": re.sub(r"\D", "", msg.to),
            "type": "text",
            "text": {"body": msg.body},
        })

    def send_template(self, msg: WhatsAppMessage) -> WhatsAppResult:
        if not msg.template_name:
            raise RuntimeError("template_name required")
        return self._send({
            "messaging_product": "whatsapp",
            "to": re.sub(r"\D", "", msg.to),
            "type": "template",
            "template": {
                "name": msg.template_name,
                "language": {"code": msg.template_language},
                "components": msg.template_components or [],
            },
        })

    # -------------------------------------------------- webhooks

    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        secret = self._cfg()["app_secret"]
        if not secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def verify_challenge(self, mode: str | None, token: str | None,
                         challenge: str | None) -> str | None:
        """GET webhook verification — returns challenge if token matches."""
        expected = self._cfg()["verify_token"]
        if mode == "subscribe" and expected and token == expected and challenge:
            return challenge
        return None

    def parse_events(self, payload: dict) -> list[WAEvent]:
        out: list[WAEvent] = []
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                # statuses (delivery/read/failed receipts)
                for st in value.get("statuses", []) or []:
                    out.append(WAEvent(
                        event_id=st.get("id", ""), kind="status",
                        message_id=st.get("id"), status=st.get("status"),
                        to_phone=st.get("recipient_id"),
                        error=(st.get("errors") or [{}])[0].get("title") if st.get("errors") else None,
                        raw=st,
                    ))
                # incoming messages
                for msg in value.get("messages", []) or []:
                    text = None
                    mtype = msg.get("type")
                    if mtype == "text":
                        text = (msg.get("text") or {}).get("body")
                    elif mtype == "button":
                        text = (msg.get("button") or {}).get("text")
                    elif mtype == "interactive":
                        inter = msg.get("interactive") or {}
                        text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title")
                    out.append(WAEvent(
                        event_id=msg.get("id", ""), kind="message",
                        from_phone=msg.get("from"),
                        message_id=msg.get("id"), text=text,
                        timestamp=msg.get("timestamp"), raw=msg,
                    ))
                # template status updates
                for ts in value.get("message_template_status_update", []) or []:
                    out.append(WAEvent(
                        event_id=ts.get("message_template_id", "") + ts.get("event", ""),
                        kind="template_status",
                        status=ts.get("event"), raw=ts,
                    ))
        return out

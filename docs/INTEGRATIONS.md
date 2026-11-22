# Integrations

All external providers are optional at runtime — missing credentials yield
honest `not_configured` / `configured: false` states, never fabricated
success. Automated tests use **mocked** providers; no live provider has
been validated end-to-end.

## WhatsApp — Meta Cloud API

**Files:** `app/integrations/whatsapp/meta_cloud.py`,
`app/services/whatsapp_templates.py`, `app/api/whatsapp.py`,
`app/api/whatsapp_webhooks.py`.

**Required env:** `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`,
`WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`,
`WHATSAPP_BUSINESS_ACCOUNT_ID`, `WHATSAPP_API_VERSION` (default `v21.0`).

**Endpoints:**
- `GET /whatsapp/templates`, `GET /whatsapp/templates/{id}` — member+
- `POST /whatsapp/templates/sync` — admin/owner (pulls all WABA templates,
  paginated, upserts by name+language+provider id)
- `POST /whatsapp/templates/{id}/send` — member+, approved templates only
- `GET|POST /webhooks/whatsapp` — Meta verification + events (signature
  via `x-hub-signature-256` HMAC-SHA256)

**Behavior:** template statuses map PENDING/APPROVED/REJECTED/PAUSED/
DISABLED → lowercase; `is_active` only when APPROVED. Outside the 24h
customer-service window only an explicitly selected approved template may
send; free-form is blocked (`template_required`). Webhook `statuses`
events update `Message` monotonically (sent→delivered→read; failed
terminal); `message_template_status_update` updates templates with an
out-of-order guard and quarantines unmatched events.

**Limitations:** live Meta validation pending credentials; template
*creation* via `submit_template()` exists in the adapter but has no UI.

## Email — Resend

**Files:** `app/integrations/email/resend.py`, `app/api/webhooks.py`.

**Required env:** `RESEND_API_KEY`, `EMAIL_FROM`,
`RESEND_WEBHOOK_SECRET` (svix `whsec_…`), `EMAIL_PROVIDER=resend`.

**Endpoints:** `POST /webhooks/email/resend` — verifies svix signature
(`svix-id`/`svix-timestamp`/`svix-signature`, 5-min tolerance), idempotent
via `WebhookEvent`.

**Events:** `email.delivered`→delivered (+conv engaged),
`email.bounced`/`email.complained`→failed + `SuppressionRecord` +
`conv.needs_human` + campaign `stats` bump,
`email.failed`→failed, `email.received`→inbound.

**Inbound:** workspace resolved from `Workspace.settings.inbound_email`
matching the recipient; ambiguous → `quarantined` (never guesses).
Duplicate provider message ids → `duplicate_inbound`.

**Limitations:** reply threading relies on `thread_id` correlation; no
explicit In-Reply-To/References header threading verified.

## LLM providers

**Files:** `app/integrations/llm/base.py`, `gemini.py`,
`app/services/llm_metering.py`.

Provider-agnostic via `get_llm()`; `LLM_PROVIDER` + `LLM_API_KEY` +
`LLM_MODEL` (Gemini adapter reads `GEMINI_API_KEY`/`GEMINI_MODEL`).
Every real call is wrapped in `MeteredLLM` → `LLMInvocation` row
(workspace/workflow/step/agent/provider/model/status/tokens/duration;
**no prompt text**). Workflow `limits.max_llm_calls` is enforced
atomically before each call (`LLMBudgetExceeded`). Heuristic fallbacks
are not counted.

**Limitations:** token counts are provider-reported; absent usage → 0
(recorded honestly, not invented). No cost estimation implemented.

## Redis — rate limiting

**File:** `app/core/ratelimit.py`.

`REDIS_URL` enables distributed limiting; `REQUIRE_REDIS_RATE_LIMIT=1`
makes production fail closed. `GET /system/status` → `rate_limit` block
reports backend/reachable/degraded without exposing the URL.

## Google Calendar

**Files:** `app/integrations/calendar/`, `app/services/appointments.py`.

`GOOGLE_CALENDAR_CLIENT_ID/SECRET/REFRESH_TOKEN/CALENDAR_ID`. Internal
scheduling always works; external calendar sync is configuration-gated.
Appointment overlap is prevented at app level (all DBs) and by a
PostgreSQL exclusion constraint where supported.

## Research providers

`GOOGLE_PLACES_API_KEY` (optional enrichment). Website enrichment has an
SSRF guard (`website_enricher.py`).

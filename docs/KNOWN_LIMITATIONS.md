# Known Limitations

Classified honestly — "requires credentials" is not "broken".

## Requires external credentials / infrastructure
- **Live Meta WhatsApp validation** — template sync/send/webhooks verified
  only with mocked Graph API responses.
- **Live Resend delivery** — webhook lifecycle verified with signed mocks;
  real deliverability untested.
- **Live LLM calls** — metering/budgets tested with stub providers; real
  token usage depends on provider `usageMetadata`.
- **PostgreSQL exclusion constraint** — migration structure tested; live
  concurrent-booking behavior needs `AGW_TEST_PG_URL`.
- **Redis distributed rate limiting** — diagnostics/fail-closed tested;
  real Redis reachability untested locally.
- **Google Calendar sync** — configuration-gated; internal scheduling works.

## Verified limitations
- **No LLM cost estimation** — token counts are provider-reported; no
  pricing table exists, so no dollar figures are shown.
- **Email threading** — relies on `thread_id` correlation; explicit
  In-Reply-To/References headers not implemented.
- **WhatsApp template creation UI** — `submit_template()` exists in the
  adapter but there is no frontend for authoring templates.
- **Prompt injection** — mitigations (delimiting, sanitization, compliance
  gates) reduce risk but cannot guarantee immunity; see
  `SECURITY_AND_PROMPT_INJECTION.md`.
- **Worker observability** — heartbeats expose status/job-name only; no
  per-job payload history (by design — avoids storing sensitive data).
- **Single-node rate limiting** — without Redis, limits are per-process.

## Planned enhancements
- Frontend template-management UI (list exists read-only in Operations).
- Cost dashboards once provider pricing is configured.
- Richer worker job-history (payload-free) if needed operationally.

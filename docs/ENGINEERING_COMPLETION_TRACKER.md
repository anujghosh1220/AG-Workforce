# AG Workforce — Engineering Completion Tracker

Mission: finish all outstanding Phase 8.7–8.9 items. Resume from this file.
Branch: `feat/workforce-completion` (PR #1 open, never merge to `main`).
Latest verified baseline: **158 backend tests passed, 2 skipped** (SQLite),
`alembic upgrade head` → `a1b2c3d4e5f6 (head)`, frontend `tsc -b` clean,
`npm run build` OK, `npm run lint` 0 errors.

Status legend: NOT_STARTED / IN_PROGRESS / IMPLEMENTED / TESTED / BLOCKED

## Authoritative workstream status

| WS | Item | Status | Evidence |
|----|------|--------|----------|
| 1 | Appointment PG concurrency | IMPLEMENTED (live PG verify BLOCKED) | block_start/end cols, `_sync_block`, IntegrityError→409, exclusion constraint migration; needs `AGW_TEST_PG_URL` |
| 2 | Worker instrumentation | IMPLEMENTED | WorkerHandle in all 5 workers; dedicated restart/stale tests still thin |
| 3 | Redis diagnostics | IMPLEMENTED | real PING, fail-closed prod; dedicated tests still thin |
| 4 | WhatsApp lifecycle | TESTED | 14 mocked tests `test_whatsapp_templates.py`; live Meta unverified |
| 5 | Resend lifecycle | TESTED | 11 mocked tests `test_resend_lifecycle.py`; live Resend unverified |
| 6 | Prompt-injection security | TESTED | `untrusted_block` wired in outreach/conversation_engine/manager_engine; 5 unit + 7 adversarial tests pass; `docs/SECURITY_AND_PROMPT_INJECTION.md` |
| 7 | LLM metering + budgets | TESTED | `MeteredLLM`+`LLMInvocation`+atomic budget; `GET /analytics/llm-usage`; 8/8 `test_llm_metering.py` |
| 8 | Frontend ops panels | TESTED | `Operations.tsx` (LLM usage, workers, WA templates, integrations); tsc/build/lint clean |
| 9 | Documentation | TESTED | ARCHITECTURE/INTEGRATIONS/DEPLOYMENT/ENVIRONMENT_VARIABLES/TESTING/KNOWN_LIMITATIONS + SECURITY_AND_PROMPT_INJECTION + README rewritten |
| 10 | Final verification | IN_PROGRESS | pytest 158/2 OK; alembic OK; tsc/build/lint OK; PG-specific tests need `AGW_TEST_PG_URL` |

## Per-workstream detail

### WS1 — Appointment concurrency — IMPLEMENTED
- `Appointment.block_start`/`block_end` synced on create/book/reschedule;
  occupied interval = [start−buffer, end+buffer).
- Migration `a1b2c3d4e5f6`: columns + backfill + PG-only
  `excl_appointment_overlap` EXCLUDE USING gist (btree_gist, tstzrange,
  partial on blocking statuses). SQLite keeps app-level checks.
- `IntegrityError`→409 handler in `main.py`.
- **Blocked:** live PG concurrency needs `AGW_TEST_PG_URL`; dedicated
  concurrency tests file not yet added.

### WS2 — Worker instrumentation — IMPLEMENTED
- `services/workers_health.py`: `WorkerHandle` stable id `type:host:pid`,
  background heartbeat (`WORKER_HEARTBEAT_SECONDS`), job-name-only tracking,
  success/failure/consecutive-failure/recovery, stale detection.
- All 5 workers (`orchestrator`, `research`, `comms`, `manager`, `reminder`)
  refactored. `GET /system/workers*` (admin) with filters + summary.
- **Gap:** dedicated per-worker restart/stale tests thin.

### WS3 — Redis diagnostics — IMPLEMENTED
- `core/ratelimit.py`: real `PING` (5s cache), `status()` reports
  backend/distributed/distributed_required/reachable/degraded/config_valid;
  prod fails closed, dev falls back to memory. No URL/credential exposure.
- **Gap:** dedicated reachable/unreachable/missing/fail-closed tests thin.

### WS4 — WhatsApp — TESTED (mocked)
- `meta_cloud.py`: `list_templates` (cursor pagination), `get_template`,
  `submit_template`, `_graph_get` (429/malformed handling).
- `services/whatsapp_templates.py`: idempotent sync (name+lang+provider_id),
  status map PENDING/APPROVED/REJECTED/PAUSED/DISABLED, approved-only
  validated send + compliance recheck.
- `api/whatsapp.py`: list/detail/sync(admin)/send(member+), workspace-scoped.
- `whatsapp_webhooks.py`: template_status→MessageTemplate, monotonic rank
  guard, quarantine unmatched.
- `outreach.send_message(...,template_name)`: outside 24h → template_required
  unless explicit approved template.
- **Limitation:** live Meta unverified; no template-creation UI.

### WS5 — Resend — TESTED (mocked svix)
- `webhooks.py`: delivered/bounced/complained/failed → Message status +
  timestamps; bounce/complaint → SuppressionRecord + needs_human +
  `_bump_campaign_stats`; received → workspace resolve via
  `settings.inbound_email`, ambiguous → `quarantined`, provider-id dedupe.
- **Limitation:** In-Reply-To/References threading not implemented.

### WS6 — Prompt security — TESTED
- `untrusted_block`/`sanitize_untrusted`/`UNTRUSTED_NOTICE` in
  `services/prompt_security.py`; wired into all external-content LLM paths.
- Enforcement (RBAC, approvals, compliance) is code-level, independent of
  prompts. `test_prompt_adversarial.py` 7/7 proves suppression/opt-out/
  cross-workspace still enforced.
- **Residual:** delimiters are advisory; see SECURITY_AND_PROMPT_INJECTION.md.

### WS7 — LLM metering — TESTED
- `llm_metering.py`: `LLMInvocation` (no prompt text), `MeteredLLM`,
  `scope()`, atomic `llm_calls_used` budget, `LLMBudgetExceeded`.
- `GET /analytics/llm-usage?days=N` — calls/failed/tokens/by_model/by_agent/
  daily for caller's workspace.
- **Limitation:** no cost estimation (no pricing data).

### WS8 — Frontend — TESTED
- `Operations.tsx`: LLM Usage, Worker Health, WhatsApp Templates,
  Integrations panels; loading/error(403)/empty states; polling.
- Fixed pre-existing `Campaigns.tsx` rules-of-hooks violation.

### WS9 — Docs — TESTED
- `docs/`: ARCHITECTURE, INTEGRATIONS, DEPLOYMENT, ENVIRONMENT_VARIABLES,
  TESTING, KNOWN_LIMITATIONS, SECURITY_AND_PROMPT_INJECTION.
- README rewritten (was stale "Phase 1").

### WS10 — Final verification — IN_PROGRESS
- Done: pytest 158/2, alembic upgrade head (SQLite), tsc -b, npm build,
  oxlint 0 errors.
- Outstanding: `AGW_TEST_PG_URL` PostgreSQL run; live provider smoke tests
  (need credentials); final git hygiene review.

## History (condensed)
- Remote commits `382096d`/`58179ad` wired prompt_security + workspace LLM
  attribution before local WS6/WS7 work — preserved via rebase.
- WS4/WS5 earlier "NOT_STARTED" sub-bullets were stale duplicates of the
  completed work above; removed in this consolidation.

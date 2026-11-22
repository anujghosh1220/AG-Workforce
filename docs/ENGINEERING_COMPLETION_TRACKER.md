# AG Workforce — Engineering Completion Tracker

Mission: finish all outstanding Phase 8.7–8.9 items. Resume from this file.
Baseline: 143 backend tests passed, 2 skipped (SQLite) — verified post-rebase
on feat/workforce-completion @ 8fddec7 (includes remote commits 382096d +
58179ad which wired prompt_security.untrusted_block into conversation_engine,
manager_engine, outreach + added tests/test_prompt_security.py).
Pushed to origin/feat/workforce-completion; PR #1 stays open.

## Session state (latest)
- WS4 WhatsApp: TESTED (14 mocked tests).
- WS5 Resend: TESTED (11 mocked tests; inbound quarantine+dedupe,
  lifecycle rollups, campaign stats).
- WS6 prompt security: PARTIALLY DONE by remote commits — untrusted_block
  wired in 3 services; verify remaining entry points (website_enricher
  output path, workflow_steps draft, research results) + adversarial tests
  coverage; residual-risk doc still needed.
- WS7 LLM metering: usage endpoint added (GET /analytics/llm-usage).
  `tests/test_llm_metering.py` CREATED (8 tests) but 5 FAILING —
  test_successful/failed/missing-token (service-level) + 2 endpoint tests.
  Patched to create real Workspace rows (FK on llm_invocations.workspace_id)
  — still failing; next session must read actual traceback
  (`pytest tests/test_llm_metering.py -x`) — likely scope()/own-session
  commit or assertion mismatch, NOT necessarily FK anymore.
  3 passing: budget_blocks_after_limit, concurrent_budget, auth-required.
- WS6: untrusted_block wired in outreach/conversation_engine/manager_engine
  (remote commits); test_prompt_security.py exists (unit-level only).
  STILL TODO: adversarial path tests, SECURITY_AND_PROMPT_INJECTION.md.
- WS8 frontend panels: NOT_STARTED.
- WS9 docs: PARTIAL (.env.example only).
- WS10 verification: pytest 143/2 OK; alembic upgrade head OK (SQLite);
  tsc/build NOT_RUN.

Status legend: NOT_STARTED / IN_PROGRESS / IMPLEMENTED / TESTED / BLOCKED

## Workstream 1 — Appointment DB concurrency — IMPLEMENTED (PG live verify BLOCKED)
- Files: `backend/app/models/__init__.py` (Appointment.block_start/block_end),
  `backend/app/services/appointments.py` (`_block_window`, `_sync_block` wired
  into create_appointment, book_slot, apply_reschedule),
  `backend/app/main.py` (IntegrityError→409 handler),
  `backend/migrations/versions/a1b2c3d4e5f6_phase8_8_concurrency_lifecycle_.py`
  (columns + backfill + PG-only `excl_appointment_overlap` EXCLUDE USING gist
  with btree_gist, tstzrange '[)', partial WHERE on blocking statuses;
  `CREATE EXTENSION IF NOT EXISTS btree_gist`).
- Buffer semantics match `availability.is_slot_available`: occupied interval
  = [start-buffer, end+buffer).
- Remaining: run `alembic upgrade head`; live PG concurrency test needs
  `AGW_TEST_PG_URL` (unset). Add dedicated tests file (adjacent slots,
  different reps, reschedule self-exclusion, cancel frees slot, buffer edge,
  concurrent threads, rollback). Migration downgrade drops constraint+columns.
- Status detail: IMPLEMENTED, TESTED only via existing suite (test_appointments
  459 lines still green). Dedicated new tests NOT_STARTED.

## Workstream 2 — Worker instrumentation — IMPLEMENTED (all 5 workers)
- `backend/app/services/workers_health.py` rewritten: `WorkerHandle`
  (stable id `type:hostname:pid`, background heartbeat thread,
  `WORKER_HEARTBEAT_SECONDS` env default 15s, `set_job`/`job()` ctx,
  `record_success`/`record_failure`, `shutdown`→status stopped,
  `start()` bumps recovery_attempts), `beat/idle/success/failure/stop/start`,
  `health()` with filters + counts (healthy/idle/degraded/stale/failed/stopped)
  + heartbeat_age_seconds. WorkerHeartbeat model gained host/pid/started_at/
  last_job_name/last_error (migration a1b2c3d4e5f6).
- Workers refactored: orchestrator_worker, comms_worker, manager_worker,
  reminder_worker, research_worker (all use `_handle()` WorkerHandle).
- Remaining: dedicated worker tests (restart, stale, per-type) NOT_STARTED.

## Workstream 3 — Redis diagnostics — IMPLEMENTED
- `backend/app/core/ratelimit.py` rewritten: real `PING` health (5s cache),
  `status()` returns backend/distributed/distributed_required/
  redis_url_configured/redis_reachable/redis_checked/degraded/config_valid/
  environment; production (`ENVIRONMENT=production` or
  `REQUIRE_REDIS_RATE_LIMIT=1`) fails closed via `RateLimiterUnavailable`
  (never silent memory downgrade); dev falls back to memory per-call when
  Redis down and reports degraded. No URL/credential exposure.
- Remaining: dedicated ratelimit tests NOT_STARTED (reachable/unreachable/
  missing/fail-closed; mock vs live Redis distinction).

## Workstream 4 — WhatsApp lifecycle — TESTED (mocked Meta only)
- `meta_cloud.py`: `list_templates()` (cursor pagination), `get_template()`,
  `submit_template()` (POST /{waba}/message_templates), `_graph_get` with
  rate-limit/malformed handling; never fabricates.
- `services/whatsapp_templates.py`: `sync_templates` (idempotent upsert by
  name+language+provider_template_id, status map PENDING/APPROVED/REJECTED/
  PAUSED/DISABLED→lowercase, rejection_reason, components JSON, last_synced_at,
  is_active only when approved), `_validate_components` (type+param checks),
  `send_approved_template` (workspace+channel check, APPROVED-only, compliance
  recheck, provider_message_id persisted, honest not_configured/failed).
- `api/whatsapp.py` (NEW, registered in main.py): GET /whatsapp/templates,
  GET /whatsapp/templates/{id}, POST /whatsapp/templates/sync (admin),
  POST /whatsapp/templates/{id}/send (member+). TemplateOut schema.
- `whatsapp_webhooks.py`: template_status events → MessageTemplate by
  provider_template_id, monotonic rank guard (pending<approved/rejected<
  paused/disabled), provider timestamp→last_synced_at, unmatched→quarantined.
- `outreach.send_message(..., template_name=None)`: outside 24h window →
  template_required unless explicit approved template; in-window unchanged.
- `tests/test_whatsapp_templates.py` (NEW, 14 tests, all Meta calls MOCKED):
  pagination, sync mapping, idempotent re-sync, not_configured, provider
  errors/malformed, approved send w/ provider id persistence, unapproved/
  pending/rejected blocked, invalid components/params, cross-workspace
  isolation, webhook signature, template-status mapping, duplicate,
  out-of-order guard, quarantine, outside-window template_required.
- Result: 127 passed / 2 skipped; `alembic upgrade head` OK on SQLite.
- Remaining: live Meta validation needs WHATSAPP_* creds; frontend panel
  deferred to frontend workstream; template submission UI not built.
- Done: `Message.provider_message_id` (+delivered_at/read_at/failed_at/
  provider_error) persisted on send in `outreach.send_message`;
  `whatsapp_webhooks.py` maps status events→Message with monotonic ranking
  (sent<delivered<read, failed terminal) + provider timestamps.
- NOT_STARTED: MetaCloudProvider.list_templates/fetch templates sync service
  + authenticated sync endpoint; template send path w/ param+language
  validation; outside-24h-window approved-template sending; template_status
  webhook→MessageTemplate.provider_status sync; MessageTemplate components/
  provider_category columns exist (migration done) but unused;
  submission-to-Meta endpoint (manual approval workflow doc).
- Files to touch: `app/integrations/whatsapp/meta_cloud.py`,
  `app/integrations/whatsapp/base.py`, `app/services/outreach.py`,
  `app/api/whatsapp_webhooks.py`, new `app/api/whatsapp.py` or extend
  compliance/system router, `app/services/` new `whatsapp_templates.py`.

## Workstream 5 — Resend lifecycle — TESTED (mocked svix signatures)
- `tests/test_resend_lifecycle.py`: 11/11 PASS — delivered/bounced/
  complained/failed mapping, unknown-id safe, duplicate webhook, bad sig 401,
  cross-workspace, inbound quarantine, inbound dedupe (provider_message_id),
  campaign stats bounce rollup.
- Fixes this session: removed shadowing local import in webhooks.py
  (UnboundLocalError), inbound route is /workspaces/current, test bug
  (duplicate provider id) resolved.
- Remaining minor: Resend reply threading headers (In-Reply-To/References)
  not explicitly verified — resend.py sends X-AGW-Message-Id only;
  thread_id correlation exists via event.thread_id→conversation.
  Document as limitation if not implemented.
- `app/api/webhooks.py` changes (THIS SESSION):
  * delivered → conv.state="engaged" (from contacted/new); bump handled below
  * bounced → SuppressionRecord + conv.needs_human + `_bump_campaign_stats("bounced")`
  * complained → SuppressionRecord + conv.needs_human + conv.state="complained"
    + `_bump_campaign_stats("complained")`
  * failed → `_bump_campaign_stats("failed")`
  * `_bump_campaign_stats(db,msg,key)`: finds Campaign rows in workspace whose
    lead_ids JSON contains the conversation's lead id; increments stats[key].
  * received branch: wid None → we.payload["quarantined"]=True +
    result {"status":"quarantined"} (never guesses workspace);
    duplicate guard: Message.provider_message_id==event.message_id →
    {"status":"duplicate_inbound"}; after handle_inbound, stamps
    provider_message_id=event.message_id on created inbound Message
    (handle_inbound returns "message_id" — verified).
  * imports: Conversation, Message, MessageStatus added at top.
- Verified: `python -c "import app.main"` OK; test_comms+test_whatsapp 18 passed.
- `tests/test_resend_lifecycle.py` CREATED (11 tests): delivered/bounced/
  complained/failed event mapping, unknown-id safety, duplicate webhook,
  bad signature 401, cross-workspace, inbound quarantine, inbound dedupe,
  campaign stats rollup. 9 PASS / 2 FAIL currently:
  * test_inbound_duplicate_guard — workspace PATCH route FIXED to
    /workspaces/current; now fails with UnboundLocalError inside
    handle_inbound/conversation_engine (inspect traceback — likely
    `wid`/var scoping in webhooks received branch or engine). DEBUG NEXT.
  * test_campaign_stats_bounce_rollup — matched=True now asserted OK;
    stats["bounced"] still None → _bump_campaign_stats found no campaign
    OR lead_ids mismatch. Campaign lead_ids stores str(l.id) (verified
    campaigns.py:47); message conv lead = same lead. DEBUG: check
    c.lead_ids content vs lid, and whether _bump_campaign_stats ran
    (maybe conv.lead lazy-load None → early return). NEXT.
- STILL TODO after fixes: run full suite, mark TESTED, then WS6-10.
- Note: resend.py In-Reply-To/References threading — NOT yet verified;
  check `OutboundEmail.headers` usage in outreach.send_message (it already
  sends X-AGW-Message-Id header; verify reply threading headers exist).
- Branch: feat/workforce-completion. Baseline 127 passed/2 skipped.
- Existing: provider_message_id persisted on send; webhooks.py maps
  delivered/bounced/complained/failed → Message status/timestamps +
  SuppressionRecord on bounce/complaint; svix signature verify +
  WebhookEvent idempotency exist; handle_inbound has opt-out→consent
  withdrawal→suppression→follow-up cancellation path.
- NEXT TASKS (in order):
  1. In webhooks.py `received` branch: persist quarantine marker when
     `_resolve_workspace` returns None (currently returns
     {"status":"unmatched_workspace"} with no record) — write
     WebhookEvent.payload["quarantined"]=True or a dedicated result;
     also when MULTIPLE workspaces could match (check _resolve_workspace
     ambiguity). Add duplicate-inbound guard: before handle_inbound,
     check Message with same provider_message_id/external_id in that
     conversation → skip ("duplicate").
  2. Campaign/conversation rollups: on delivered/bounced/complained/
     failed, if msg.conversation_id → update Conversation state if needed
     (e.g. hard bounce → needs_human or state="bounced"); campaign stats —
     check Campaign model fields (sent/delivered/bounced counters?) before
     implementing; update only real columns.
  3. Threading: ensure In-Reply-To/References headers already set by
     Resend provider on replies (check resend.py send path); inbound
     thread_id resolution already uses event.thread_id — verify
     conversation.external_thread_id matching is workspace-scoped.
  4. Tests `tests/test_resend_lifecycle.py`: each event type, duplicate
     webhook, invalid signature (existing svix tests?), unknown provider
     id, cross-workspace, inbound dedupe, quarantine.
- Files: `app/api/webhooks.py`, `app/services/conversation_engine.py`
  (handle_inbound dedupe), `app/integrations/email/resend.py`,
  `tests/test_resend_lifecycle.py` (new).
- Baseline verified 2025-…: 127 passed / 2 skipped on branch
  feat/workforce-completion (commit pending).
- Done: provider_message_id persisted on send; `api/webhooks.py` maps
  delivered/bounced/complained/failed → Message status/timestamps +
  SuppressionRecord on bounce/complaint; unknown IDs safe; svix signature
  verification already existed; WebhookEvent idempotency existed.
- NOT_STARTED: campaign/conversation status rollups on events; inbound
  threading improvements + quarantine flag for ambiguous inbound (currently
  returns unmatched_workspace without persisting quarantine marker);
  duplicate inbound message guard (check provider_message_id/external_id
  before handle_inbound); tests for all event types.

## Workstream 6 — Prompt-injection security — IMPLEMENTED (tests pending)
- External prospect facts, operator-supplied draft instructions, conversation
  history/inbound text, approved knowledge, and aggregated manager metrics are
  wrapped with `untrusted_block` before insertion into LLM user prompts.
  Matching system prompts include `UNTRUSTED_NOTICE`; delimiter neutralization
  and size caps remain active.
- Direct outreach, inbound-analysis and manager-insight LLM calls now use
  `llm_metering.scope(workspace_id=...)` for workspace attribution.
- Remaining: adversarial tests and residual-risk documentation. Workflow reply
  drafting delegates to the secured `outreach.generate_reply_draft` path.
- Scanner remains advisory. Approval gates, capability checks and immediate
  pre-send compliance checks remain the enforcement layer.

## Workstream 7 — LLM metering — IMPLEMENTED (core) / PARTIAL wiring
- `app/services/llm_metering.py`: `LLMInvocation` model (workspace_id,
  workflow_id, step_id, agent_id, agent_slug, provider, model, status,
  tokens_prompt/completion, duration_ms, error≤300 chars, created_at —
  NO prompt contents); `MeteredLLM` wraps provider; `scope()` contextvar;
  `_budget_check_and_increment` atomic `UPDATE workflows SET
  llm_calls_used=llm_calls_used+1 WHERE llm_calls_used<max` →
  `LLMBudgetExceeded`; failed calls recorded; own-session commit so caller
  rollback doesn't lose the record.
- `get_llm()` in `integrations/llm/base.py` now returns MeteredLLM always.
- `gemini.py` captures `usageMetadata`→`last_usage`/`last_model`.
- `workflow_steps._run` wraps executors in `llm_metering.scope(...)`.
- `Workflow.llm_calls_used` column + migration added.
- NOT_STARTED: `llm_metering.scope` in direct service call sites
  (outreach.py, conversation_engine.py, manager_engine.py) so workspace_id
  attribution is populated for non-workflow calls; LLM usage API endpoint
  (e.g. GET /analytics/llm-usage or /system/llm-usage); metering tests
  (success/failure/retry/concurrency/exhaustion).

## Workstream 8 — Frontend ops panels — NOT_STARTED
- `frontend/src/pages/Workforce.tsx` exists (dark dashboard, workflows,
  autonomy, event feed). Add panels: worker health (GET /system/workers +
  /summary), rate-limit status (GET /system/status→rate_limit), WhatsApp
  templates (needs WS4 endpoint), email event stats, stage history/time-in-
  stage (needs WS5/analytics endpoint), LLM usage (needs WS7 endpoint),
  appointment conflict surfacing (already 409→ApiError). Admin-only gating
  via auth user role. Keep polling modest (existing 5–60s pattern).
- `frontend/src/lib/api.ts` has `api.get/post/put` helpers + ApiError.

## Workstream 9 — Docs — PARTIAL
- `backend/.env.example` updated (REDIS_URL, REQUIRE_REDIS_RATE_LIMIT,
  WORKER_HEARTBEAT_SECONDS, WHATSAPP_BUSINESS_ACCOUNT_ID/API_VERSION,
  LLM_PROVIDER/GEMINI_*).
- NOT_STARTED: README updates, backend setup guide, migration guide,
  worker ops guide, WhatsApp setup guide, Resend webhook guide, LLM config
  guide, security doc, production checklist. Create under `docs/`.

## Workstream 10 — Final verification — PARTIAL
- `python -m pytest tests -q`: 113 passed, 2 skipped (after workers_health
  fix; rerun to confirm still green).
- NOT_RUN: `alembic upgrade head`, `npx tsc -b`, `npm run build`,
  PG-specific tests (AGW_TEST_PG_URL unset).

## Known issues / notes for next session
- `workers_health.success()` no longer sets status idle (test expects
  "running" after beat+success) — intentional; idle set via `idle()`/handle.
- `Message.provider_message_id` has `index=True` in model AND migration
  creates `ix_messages_provider_id` — consistent.
- `stage_history.py` mapper writes history with actor="system";
  `record_manual` exists for explicit attribution — wire contextvars next.
- `analytics.py` has no stage-history/time-in-stage endpoint yet.
- No git commits made; all changes are working-tree only.
- Python 3.14, SQLAlchemy 2, FastAPI; tests use SQLite file via conftest
  (`Base.metadata.create_all`, ENVIRONMENT=test, workers disabled).

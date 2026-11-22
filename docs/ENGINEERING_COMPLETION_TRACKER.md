# AG Workforce — Engineering Completion Tracker

Mission: finish all outstanding Phase 8.7–8.9 items. Resume from this file.
Baseline before this effort: 113 backend tests passed, 2 skipped (SQLite).

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

## Workstream 4 — WhatsApp lifecycle — PARTIAL
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

## Workstream 5 — Resend lifecycle — PARTIAL
- Done: provider_message_id persisted on send; `api/webhooks.py` maps
  delivered/bounced/complained/failed → Message status/timestamps +
  SuppressionRecord on bounce/complaint; unknown IDs safe; svix signature
  verification already existed; WebhookEvent idempotency existed.
- NOT_STARTED: campaign/conversation status rollups on events; inbound
  threading improvements + quarantine flag for ambiguous inbound (currently
  returns unmatched_workspace without persisting quarantine marker);
  duplicate inbound message guard (check provider_message_id/external_id
  before handle_inbound); tests for all event types.

## Workstream 6 — Prompt-injection security — PARTIAL (module exists, NOT WIRED)
- `app/services/prompt_security.py` created: `sanitize_untrusted`,
  `scan_injection` (heuristic patterns), `untrusted_block(label, content)`
  with delimiter neutralization + 12k char cap, `UNTRUSTED_NOTICE`.
- NOT_STARTED: apply `untrusted_block` in `outreach.generate_intro_draft`,
  `outreach.generate_reply_draft`, `conversation_engine.analyze_inbound`,
  `manager_engine.generate_insights`, `workflow_steps.exec_draft_reply`;
  add `UNTRUSTED_NOTICE` to system prompts; adversarial tests; residual-risk
  doc section.
- Capability registry (`capabilities.py`) and pydantic output validation
  already enforce structure; compliance re-checks already immediate pre-send.

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

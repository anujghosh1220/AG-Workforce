# Testing

## Backend
```bash
cd backend
python -m pytest tests -q          # full suite (SQLite, mocked providers)
python -m pytest tests/test_llm_metering.py -q
python -m pytest tests/test_prompt_adversarial.py -q
python -m pytest tests/test_whatsapp_templates.py -q   # mocked Meta
python -m pytest tests/test_resend_lifecycle.py -q     # mocked svix
python -m alembic upgrade head     # migrations on SQLite
python -m alembic current
```

**Test DB:** SQLite file via `DATABASE_URL` override in `tests/conftest.py`;
`AGW_DISABLE_WORKER=1` in tests. Provider HTTP is monkeypatched — no live
Meta/Resend/LLM calls are made or claimed.

**PostgreSQL:** set `AGW_TEST_PG_URL=postgresql+psycopg://…` to a disposable
DB to run the appointment exclusion-constraint tests; otherwise those tests
skip (structure is still migration-tested on SQLite).

## Coverage by area
- `test_auth`, `test_hardening` — JWT, RBAC, workspace isolation, expiry
- `test_appointments` — booking, conflicts, reschedule
- `test_comms`, `test_resend_lifecycle` — email lifecycle, quarantine,
  dedupe, suppression, campaign rollups
- `test_whatsapp`, `test_whatsapp_templates` — Meta lifecycle (mocked),
  template sync/send, webhooks
- `test_workflows` — orchestrator, heartbeats, worker health
- `test_prompt_security`, `test_prompt_adversarial` — injection boundaries
- `test_llm_metering` — invocation records, budgets, concurrency, endpoint
- `test_manager`, `test_research`, `test_crm_work` — agent services

## Frontend
```bash
cd frontend
npx tsc -b        # type check
npm run lint      # oxlint
npm run build     # production bundle
```
No frontend unit-test runner is configured; verification is type-check +
build + manual API integration.

## Not covered by automation
- Live provider calls (Meta, Resend, LLM, Google) — credentials required
- Live PostgreSQL concurrency — needs `AGW_TEST_PG_URL`
- Live Redis distributed limiting — needs `REDIS_URL`
- Browser E2E — no Playwright/Cypress configured

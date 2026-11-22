# Deployment

## Prerequisites
- Python 3.11+ and Node 20+
- PostgreSQL 15+ (production) — SQLite works for dev/tests only
- Redis 7+ (production rate limiting)
- Provider credentials (see `ENVIRONMENT_VARIABLES.md`) — all optional
  for boot; features degrade honestly without them

## Backend
```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp .env.example .env   # fill in values
python -m alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Frontend
```bash
cd frontend
npm install
npm run dev        # dev server (proxies /api -> localhost:8000)
npm run build      # production bundle -> dist/
```
Serve `dist/` behind any static host; set the API proxy/rewrite so
`/api/*` reaches the backend. `VITE_API_URL` is not used — the client
always calls `/api/v1` relative (see `src/lib/api.ts`).

## Database
- Set `DATABASE_URL=postgresql+psycopg://user:pass@host:5432/agworkforce`.
- `python -m alembic upgrade head` applies all migrations including the
  `btree_gist` appointment exclusion constraint (PostgreSQL-only; skipped
  on SQLite).
- **Do not** use `AGW_ALLOW_CREATE_ALL=1` in production except as a
  documented emergency path — it bypasses migration discipline.

## Workers
Five workers run inside the API process by default (disable with
`AGW_DISABLE_WORKER=1`). For production, run them as separate processes or
a process manager (systemd/containers) so a worker crash doesn't take down
the API. Each writes heartbeats visible at `GET /api/v1/system/workers`.

## Redis / rate limiting
Set `REDIS_URL=redis://host:6379/0` and `REQUIRE_REDIS_RATE_LIMIT=1` in
production. Without Redis the limiter falls back to in-memory (per-process)
— acceptable for single-instance dev, **not** for multi-instance prod.

## Health checks
- `GET /api/v1/system/status` — integrations, workers, rate-limit state
- `GET /api/v1/system/workers/summary` — worker counts (admin)

## HTTPS & webhooks
Provider webhooks require a public HTTPS URL:
- Resend: `POST /api/v1/webhooks/email/resend` (svix-signed)
- Meta WhatsApp: `GET|POST /api/v1/webhooks/whatsapp` (HMAC + verify token)

## Validation not possible without real infra
- Live PostgreSQL exclusion-constraint concurrency (needs `AGW_TEST_PG_URL`)
- Live Redis distributed limiting
- Live Meta/Resend/LLM/Calendar calls — all verified via mocks only

## Upgrade / rollback
Alembic migrations are additive. Before upgrade: backup DB
(`pg_dump`). Rollback = restore backup + `git checkout` prior release;
migrations have `downgrade()` but test them on staging first.

# AG Workforce

AI Workforce Management Platform — Phase 1.

A SaaS platform for hiring, managing and supervising AI employees. Phase 1 delivers the full management infrastructure and CRM: authentication, multi-tenant workspaces, a configurable roster of five AI employees, lead CRM, sales pipeline, tasks, human-approval queue, notifications, analytics and product knowledge management. Autonomous agent execution and external messaging integrations are intentionally **not** live yet — the architecture is built so they can be added safely later.

## Stack

| Layer    | Tech |
|----------|------|
| Frontend | React 19, Vite, TypeScript, Tailwind CSS v4, TanStack Query, Recharts, React Router |
| Backend  | Python 3.12+, FastAPI, SQLAlchemy 2.x, Pydantic v2, Alembic |
| Database | PostgreSQL (Supabase-compatible) — SQLite supported for local dev/tests |
| Auth     | JWT (PyJWT) + Argon2 password hashing |

## Repository layout

```
ag-workforce/
├── frontend/          # React SPA
├── backend/
│   ├── app/
│   │   ├── api/       # versioned REST routers (/api/v1)
│   │   ├── core/      # config, database, security
│   │   ├── models/    # SQLAlchemy models (all workspace-scoped tables)
│   │   ├── schemas/   # Pydantic request/response schemas
│   │   ├── services/  # helpers + agent execution foundation
│   │   ├── agents/    # (future) agent runtimes
│   │   ├── integrations/ # (future) Gemini, Resend, WhatsApp, Calendar
│   │   └── main.py
│   ├── migrations/    # Alembic
│   └── tests/         # pytest suite
├── docker-compose.yml
├── .env.example
└── README.md
```

## Quick start

### Option A — Docker Compose (PostgreSQL included)

```bash
cp .env.example .env        # set a strong SECRET_KEY
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs
- PostgreSQL: localhost:5432

### Option B — Local development

Backend:

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp ..\.env.example .env        # or export env vars
# For a quick local run without Postgres, use sqlite:
#   DATABASE_URL=sqlite:///./agworkforce.db
alembic upgrade head           # apply migrations
uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173, proxies /api -> :8000
```

Register at `/register` — a workspace and the five default AI employees
(Lead Researcher, Sales Executive, Communication Manager, Executive
Assistant, Sales Manager) are created automatically.

## Environment variables

See `.env.example`. Never commit `.env`. Key variables:

- `DATABASE_URL` — e.g. `postgresql+psycopg://user:pass@host:5432/db` or `sqlite:///./app.db`
- `SECRET_KEY` — long random string for JWT signing
- `ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_ALGORITHM`
- `CORS_ORIGINS` — comma-separated allowed origins

## API overview (all under `/api/v1`, Bearer JWT required except auth)

- `POST /auth/register` · `POST /auth/login` · `GET /auth/me`
- `GET /workspaces/current`
- `GET|POST /agents` · `GET|PATCH /agents/{id}` · `GET /agents/{id}/runs`
- `GET|POST /leads` (search, filter, sort, paginate) · `GET|PATCH|DELETE /leads/{id}`
- `PATCH /leads/{id}/stage` · `GET /pipeline` · `POST /pipeline/bulk-stage`
- `GET|POST /contacts` · `GET|POST /lead-sources`
- `GET /conversations`
- `GET|POST /tasks` · `PATCH /tasks/{id}`
- `GET|POST /approvals` · `PATCH /approvals/{id}` (owner/admin review)
- `GET /activities` · `GET /notifications` · `PATCH /notifications/{id}/read` · `POST /notifications/read-all`
- `GET /analytics/overview`
- `GET|POST /products` · `PATCH /products/{id}`
- `GET|POST /knowledge` · `PATCH /knowledge/{id}`

## Security model

- Argon2 password hashing; JWT access tokens carry `sub` (user) + `wid` (workspace).
- Every workspace-owned table has `workspace_id`; every query is scoped to the
  authenticated workspace — the frontend can never choose a workspace.
- Role checks: owner/admin/member/viewer; destructive and review actions are
  restricted to owner/admin.
- `channel_consents`, `suppression_records` and `message_templates` tables exist
  for compliance before any outbound messaging ships.
- `audit_logs` record important actions; `activities` form the user-facing feed.

## Approvals & AI employees (Phase 1 boundary)

- Approval records are managed (create/review) but **never execute** external
  actions. `app/services/agent_service.py` defines the future execution
  pipeline: permission check → knowledge load → run record → approval gate →
  result + token logging.
- Agent cards/config are real DB records; no fake autonomous behaviour is
  simulated.

## Testing

```bash
cd backend
pytest tests -q
```

Covers registration, login, invalid credentials, protected routes, workspace
isolation, lead CRUD/filtering/sorting/pagination, pipeline transitions,
bulk stage moves, task lifecycle, approval lifecycle, notifications and
analytics derived from real records (17 tests).

## Phase 1.5 hardening notes

- **PostgreSQL validated**: models use portable `Uuid` + `JSON`→`JSONB` variants;
  Alembic `upgrade head` verified against PostgreSQL 18 (all 20 tables, native
  enums, JSONB payloads). Live PG test: `AGW_TEST_PG_URL=... pytest tests/test_postgres_compat.py`
- **Registration policy**: public sign-up is disabled by default when
  `ENVIRONMENT=production`; `ALLOW_PUBLIC_REGISTRATION` overrides, and
  `REGISTRATION_INVITE_CODE` can require an invite code.
- **Rate limiting**: `/auth/login` and `/auth/register` are limited to
  20 req/min per IP (disabled when `ENVIRONMENT=test`).
- **Request logging**: structured request logs (method/path/status/ms) — never
  bodies, tokens or passwords.
- **Health checks**: `GET /health` and `GET /health/db`.
- **Production guard**: the app refuses to start with a default `SECRET_KEY`
  when `ENVIRONMENT=production`.
- **Permission catalog**: `app/services/permissions.py` classifies every agent
  action low/medium/high risk; high risk always requires approval.
  `GET /agents/{id}/policy` exposes the effective policy.
- **Compliance gate**: `app/services/compliance.py` + `/compliance/*` endpoints
  enforce suppression lists, withdrawn consent and do-not-contact stage before
  any outbound agent action. `agent_service.execute_task` cannot bypass it.
- **Analytics**: `messages_received`, `meetings_booked`, `response_rate` are
  computed from real message/lead records — no inferred counts.

## Production checklist (before public deployment)

- [ ] Rate limiting, email verification, password recovery
- [ ] Token rotation / refresh strategy
- [ ] Backup policy + monitoring/alerting
- [ ] Managed PostgreSQL (e.g. Supabase) with pooled connections
- [ ] Secrets management; rotate `SECRET_KEY`

## Email integration (Phase 3.5)

Provider: **Resend**. Required env vars (never commit real values):

- `RESEND_API_KEY` � send + receiving API key
- `RESEND_WEBHOOK_SECRET` � svix signing secret for `POST /api/v1/webhooks/email/resend`
- `EMAIL_FROM` � verified sender, e.g. `Sales <hello@yourdomain.com>`
- `EMAIL_PROVIDER=resend` (default)
- `GEMINI_API_KEY`, `GEMINI_MODEL` � optional LLM; heuristic fallback without it

DNS for sending domain (example.com):
- DKIM: TXT `resend._domainkey` (provided by Resend dashboard)
- SPF: TXT/MX per Resend instructions (include `_spf.resend.com` or their MX)
- DMARC: TXT `_dmarc` e.g. `v=DMARC1; p=quarantine; rua=mailto:dmarc@yourdomain.com`
- Domain verification: add records Resend shows, then verify in dashboard � do NOT claim verified until Resend confirms.

Inbound email: Resend "Receiving" must be enabled on the verified domain and
webhook pointed at `https://<host>/api/v1/webhooks/email/resend`. Set the
workspace inbound address via `PATCH /api/v1/workspaces/current`
`{"inbound_email": "hello@yourdomain.com"}` so replies resolve to the workspace.

Status check (no secrets exposed): `GET /api/v1/system/status`

# AG Workforce

An autonomous AI-powered sales department for a B2B cafe-management &
ordering software product. Five AI employees - Lead Researcher, Sales
Executive, Communication Manager, Executive Assistant, Sales Manager - are
coordinated by a central orchestrator running persistent, auditable
workflows.

## Features

- **Lead research & enrichment** - discovery, dedupe, qualification scoring
- **CRM & pipeline** - leads, opportunities, stages with history analytics
- **Conversations** - unified email + WhatsApp inbox with intent analysis
- **Outreach** - personalized drafts, approval gates, compliance enforcement
- **Campaigns** - batch outreach with eligibility checks and authorization
- **Scheduling** - demo booking, calendar integration, reminders
- **Sales Manager** - funnel analytics, bottleneck detection, insights
- **Workforce Command Centre** - orchestrated workflow execution + events
- **Operations dashboard** - LLM usage metering, worker health, WhatsApp
  template status, integration configuration
- **Compliance** - consent, suppression, opt-out, 24h WhatsApp window,
  pre-send rechecks, prompt-injection defenses

## Stack

| Layer | Tech |
|---|---|
| Frontend | React 19, Vite, TypeScript, Tailwind v4, TanStack Query |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2, Pydantic v2, Alembic |
| Database | PostgreSQL (prod) / SQLite (dev+tests) |
| Auth | JWT + Argon2, workspace-scoped RBAC |
| Workers | 5 instrumented background workers + heartbeats |
| Rate limit | in-memory / Redis (production fail-closed) |

## Quick start

```bash
# Backend
cd backend && pip install -r requirements.txt
cp .env.example .env
python -m alembic upgrade head
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

App: http://localhost:5173 - API docs: http://localhost:8000/docs

## Repository layout

```
+-- frontend/src/pages/   # 17 pages incl. Operations dashboard
+-- backend/app/
|   +-- api/              # /api/v1 routers
|   +-- services/         # outreach, compliance, metering, security
|   +-- tasks/            # 5 workers
|   +-- integrations/     # llm, resend, meta whatsapp, calendar
|   +-- models/           # workspace-scoped SQLAlchemy models
+-- backend/migrations/   # Alembic chain
+-- docs/                 # architecture, integrations, deployment
+-- docker-compose.yml
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Integrations](docs/INTEGRATIONS.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Environment variables](docs/ENVIRONMENT_VARIABLES.md)
- [Testing](docs/TESTING.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)
- [Security & prompt injection](docs/SECURITY_AND_PROMPT_INJECTION.md)
- [Engineering tracker](docs/ENGINEERING_COMPLETION_TRACKER.md)

## Testing

```bash
cd backend && python -m pytest tests -q   # 158 passed, 2 skipped
cd frontend && npx tsc -b && npm run build && npm run lint
```

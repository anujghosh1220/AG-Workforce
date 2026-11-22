# Environment Variables

Cross-checked against `backend/app` source and `backend/.env.example`.
Frontend has no build-time env vars (API client uses relative `/api/v1`).

## Application / runtime
| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATABASE_URL` | prod | `sqlite:///./agworkforce.db` | Use `postgresql+psycopg://…` in prod |
| `SECRET_KEY` | prod | `dev-secret-change-me` | JWT signing — set a long random value |
| `JWT_ALGORITHM` | no | `HS256` | |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | no | `60` | |
| `API_PREFIX` | no | `/api/v1` | |
| `CORS_ORIGINS` | no | `http://localhost:5173` | comma-separated |
| `ENVIRONMENT` | no | `development` | `production` enables fail-closed rate limiting |
| `AGW_ALLOW_CREATE_ALL` | no | unset | `1` = allow `create_all` in prod (escape hatch) |

## Auth / registration
| Variable | Required | Default | Notes |
|---|---|---|---|
| `ALLOW_PUBLIC_REGISTRATION` | no | unset | allow open signup |
| `REGISTRATION_INVITE_CODE` | no | unset | invite-gated registration |

## Workers
| Variable | Required | Default | Notes |
|---|---|---|---|
| `AGW_DISABLE_WORKER` | no | unset | `1` disables all 5 workers (tests) |
| `WORKER_HEARTBEAT_SECONDS` | no | `15` | heartbeat interval |

## Rate limiting
| Variable | Required | Default | Notes |
|---|---|---|---|
| `REDIS_URL` | prod | unset | `redis://host:6379/0` |
| `REQUIRE_REDIS_RATE_LIMIT` | prod | unset | `1` = fail closed without Redis |

## Email — Resend
| Variable | Required | Default | Notes |
|---|---|---|---|
| `EMAIL_PROVIDER` | no | `resend` | |
| `RESEND_API_KEY` | for email | unset | `re_…` |
| `EMAIL_FROM` | for email | unset | verified sender |
| `RESEND_WEBHOOK_SECRET` | for webhooks | unset | svix `whsec_…` |

## WhatsApp — Meta Cloud
| Variable | Required | Default | Notes |
|---|---|---|---|
| `WHATSAPP_ACCESS_TOKEN` | for WA | unset | system-user token |
| `WHATSAPP_PHONE_NUMBER_ID` | for WA | unset | |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | for sync | unset | WABA id for template APIs |
| `WHATSAPP_APP_SECRET` | for webhooks | unset | HMAC signature |
| `WHATSAPP_VERIFY_TOKEN` | for webhooks | unset | subscription verify |
| `WHATSAPP_API_VERSION` | no | `v21.0` | Graph API version |

## LLM
| Variable | Required | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | for AI | unset | e.g. `gemini` |
| `LLM_API_KEY` | for AI | unset | provider key |
| `LLM_MODEL` | no | provider default | |
| `GEMINI_API_KEY` | for Gemini | unset | |
| `GEMINI_MODEL` | no | `gemini-2.0-flash` | |
| `GOOGLE_API_KEY` | for Gemini | unset | alt key var |

## Google Calendar
| Variable | Required | Default | Notes |
|---|---|---|---|
| `GOOGLE_CALENDAR_CLIENT_ID` | for sync | unset | |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | for sync | unset | |
| `GOOGLE_CALENDAR_REFRESH_TOKEN` | for sync | unset | |
| `GOOGLE_CALENDAR_ID` | for sync | unset | |

## Research / enrichment
| Variable | Required | Default | Notes |
|---|---|---|---|
| `GOOGLE_PLACES_API_KEY` | optional | unset | lead enrichment |
| `SCORING_WEIGHTS` | no | unset | JSON lead-scoring weights |

## Test-only
`AGW_TEST_PG_URL` — points pytest at a disposable PostgreSQL for the
exclusion-constraint tests (skipped otherwise).

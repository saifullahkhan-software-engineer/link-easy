# Running LinkEasy locally (recommended)

**LinkEasy works best on your own machine.** The hosted demo is deliberately
reduced; a local install has no such limits.

Stuck on any of this? Email **saifullahkhanofficial1@gmail.com** and we'll help
you get it running.

## Why local is better

| | Hosted demo | Local |
|---|---|---|
| Scheduled campaign steps | ❌ off | ✅ on |
| Recurring feed / WhatsApp scans | ❌ off | ✅ on |
| Celery Beat (the timer process) | not started | runs |
| LinkedIn automation | ❌ needs a residential proxy | ✅ works — your own IP |
| Manual/on-demand scans | ✅ works | ✅ works |
| WhatsApp live chat & scanner | ✅ works | ✅ works |
| Browser memory limits | one small shared container | your machine |

The two differences that matter:

1. **Your IP is residential.** LinkedIn blocks sign-ins from datacenter IP
   ranges, which is why LinkedIn automation is disabled on the hosted demo (see
   [linkedin_availability.md](linkedin_availability.md)). From your own
   connection it just works — no proxy needed.
2. **Nothing is competing for memory.** Every automation task launches a real
   Chromium (~400–600 MB). Locally you can run several; on a free-tier
   container the API, worker and browser share one small box, so unattended
   timers are switched off to keep it from OOM-killing itself.

## Quick start (Docker — easiest)

Requires Docker and Docker Compose.

```bash
git clone https://github.com/saifullahkhan-software-engineer/link-easy.git
cd link-easy
docker compose up --build
```

That starts Postgres, Redis, the API, the Celery worker **and** Celery Beat.
`ENVIRONMENT=production` is already set in `docker-compose.yml`, so every
feature — including scheduling — is on.

Then the frontend:

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Before anything real, replace the placeholder secrets in `docker-compose.yml`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"   # CREDENTIAL_ENCRYPTION_KEY
python -c "import secrets; print(secrets.token_hex(32))"   # JWT_SECRET
```

## Manual setup (no Docker)

You need Python 3.11+, Node 18+, Postgres and Redis.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
patchright install chromium        # the automation browser
```

Create a `.env` in the repo root:

```env
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/linkeasy
REDIS_URL=redis://localhost:6379/0
JWT_SECRET=<token_hex(32)>
CREDENTIAL_ENCRYPTION_KEY=<token_hex(32)>
RESEND_API_KEY=<your key, or any string if you don't need email>
FROM_EMAIL=noreply@yourdomain.com
PASSWORD_RESET_URL=http://localhost:5173/reset-password
BACKEND_CORS_ORIGINS=http://localhost:5173
ENVIRONMENT=development
```

`ENVIRONMENT=development` (or `production`) keeps every feature enabled — only
the literal value `deployment` reduces the app.

Run each in its own terminal:

```bash
# 1. API (applies migrations on startup)
uvicorn main:app --reload --port 8000

# 2. Celery worker — runs the browser automation
celery -A worker.celery_app worker --loglevel=info --concurrency=1 --pool=prefork

# 3. Celery Beat — REQUIRED for scheduled steps and recurring scans
celery -A worker.celery_app beat --loglevel=info --schedule=/tmp/linkeasy-celerybeat-schedule

# 4. Frontend
cd frontend && npm install && npm run dev
```

Open http://localhost:5173.

> Without Beat (step 3) the app still works, but nothing fires on a timer —
> that is exactly the hosted demo's configuration.

## Verifying everything is on

```bash
curl -s http://localhost:8000/api/v1/features | python -m json.tool
```

A healthy local install reports:

```json
{
  "linkedin":       { "enabled": true },
  "whatsapp":       { "enabled": true },
  "scheduled_jobs": { "enabled": true, "message": null },
  "deployment":     { "is_demo": false, "notice": null }
}
```

`is_demo: false` means the "run it locally" banner stays hidden. If
`scheduled_jobs.enabled` is `false`, either `ENVIRONMENT=deployment` or
`SCHEDULED_JOBS_ENABLED=false` is set.

LinkedIn is off by default even locally, because most people should read the
proxy note first. Turn it on with:

```env
LINKEDIN_ENABLED=true
```

From a home connection it works without a proxy.

## Troubleshooting

**Chromium won't launch** — run `patchright install chromium`. On bare Linux
also `patchright install-deps chromium`.

**"Scheduled and recurring jobs are turned off"** — Beat isn't running, or
`ENVIRONMENT=deployment`/`SCHEDULED_JOBS_ENABLED=false` is set. Check
`/api/v1/features`.

**Jobs stay "active" but never run** — the worker is down. It, not the API,
executes tasks.

**LinkedIn shows a CAPTCHA / checkpoint** — you're on a VPN or a hosted box.
Use a normal home connection, or configure a residential proxy on the account.

**Postgres/Redis refused** — `docker compose up postgres redis` starts just
those two if you're running the app outside Docker.

Still stuck? **saifullahkhanofficial1@gmail.com**

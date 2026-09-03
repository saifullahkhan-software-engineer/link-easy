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

## Social post scheduler OAuth

The scheduler can connect YouTube, Instagram, TikTok and Facebook Pages. Each
provider needs its own OAuth app credentials (set them in the `.env` file for
the manual setup above, or in the `.env` file next to `docker-compose.yml` for
the Docker setup). A platform with empty credentials is reported as "not
configured" and its connect button is simply disabled — nothing else breaks.

```env
# YouTube (Google Cloud Console → OAuth client of type "Web application")
YOUTUBE_CLIENT_ID=
YOUTUBE_CLIENT_SECRET=
YOUTUBE_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback

# Instagram (Meta for Developers app)
INSTAGRAM_APP_ID=
INSTAGRAM_APP_SECRET=
INSTAGRAM_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/instagram/callback

# Facebook Page (same Meta app)
FACEBOOK_APP_ID=
FACEBOOK_APP_SECRET=
FACEBOOK_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/facebook/callback

# TikTok (TikTok for Developers app)
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
TIKTOK_REDIRECT_URI=http://localhost:8000/api/v1/social-scheduler/platforms/tiktok/callback

# Public base URL of this API (no trailing slash). Used to build absolute
# video URLs handed to Instagram and, when a *_REDIRECT_URI is left empty, the
# default callback URL. For local dev: http://localhost:8000
PUBLIC_API_URL=http://localhost:8000

# Where uploaded videos are stored locally.
UPLOAD_DIR=./uploads/social

# Optional: where the browser lands after a successful OAuth callback
# (the frontend settings page). Defaults to the first BACKEND_CORS_ORIGINS
# origin + /app/social-scheduler/settings.
SOCIAL_OAUTH_RETURN_URL=http://localhost:5173/app/social-scheduler/settings
```

Register the `*_REDIRECT_URI` values **exactly** as written above in each
provider's console. They all point at the API on port `8000` — the frontend
dev-server port (`5173` by default, or `3000` if you configured it) is only the
page the browser is sent to *after* the callback completes
(`SOCIAL_OAUTH_RETURN_URL`). Never put the frontend port in a provider's
redirect URI; the provider callback must reach the API or the token exchange
(and CSRF state check) cannot run.

Notes:

* YouTube uses PKCE automatically: the app generates one code verifier at
  authorization time, signs it into the short-lived OAuth `state` JWT and
  reuses the exact same verifier when exchanging the code. Re-using a Google
  authorization code is never valid — each code is single-use, so always start
  a fresh connection from the UI.
* `PUBLIC_API_URL` must be reachable from the server itself for Instagram
  publishing; for local dev `http://localhost:8000` is fine.
* Credentials are never written to logs or returned by the API; tokens are
  AES-256-GCM encrypted before they touch the database.

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

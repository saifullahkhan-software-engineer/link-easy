# Railway deployment

LinkEasy needs **three processes** — the FastAPI API, a Celery worker and Celery
Beat. On Railway they run **inside one service/container** via `start.sh`.

## Why one service, not three

A Railway volume attaches to exactly **one** service, and all three processes
must share the same durable Chromium profile directory (`PROFILE_STORAGE_DIR`):

* The **API** owns the browser during connect — `POST /api/v1/linkedin/account`
  and `POST /api/v1/whatsapp/connect` launch Chromium in-process (the old
  Celery `connect_whatsapp` task is retired).
* The **worker** reopens those same profiles for campaign sessions, feed
  scrolls and WhatsApp scans.

Split into separate services, the worker would get its own empty filesystem and
open a fresh WhatsApp profile showing a QR code instead of your live session.
The Redis locks in `worker/profile_lock.py` already serialize access across
processes, so co-locating them is safe.

## What breaks with API-only (no worker/beat)

Connecting still works, but everything queued afterwards silently disappears:

| Feature | Task | Without a worker |
|---|---|---|
| Campaign start | `tasks.run_account_session` | queued to Redis, never consumed — API says "Campaign started", nothing runs |
| Delayed campaign steps | `tasks.dispatch_due_account_sessions` (Beat, 60s) | never fires |
| Feed scroll | `tasks.run_feed_scroll`, `tasks.dispatch_due_feed_scans` | dead |
| WhatsApp scan + forward | `tasks.check_whatsapp_messages`, `tasks.dispatch_due_whatsapp_scans` | dead — QR scans fine, then no messages are ever scanned or forwarded |

Verify with `GET /api/v1/system/queues/celery-inspect` (empty when no worker is
up) and `GET /api/v1/system/queues/overview` for queue backlog.

## Services to create

1. **Postgres** — Railway plugin.
2. **Redis** — Railway plugin. **Required**: every connect path takes a
   `profile_lock` in Redis first, so a missing/unreachable `REDIS_URL` makes
   both LinkedIn and WhatsApp connect fail with a confusing 500.
3. **App** — this repo, built from the `Dockerfile` (`railway.json` sets
   `builder: DOCKERFILE`).

## Volume — recommended, with a safe fallback

Add a persistent volume to the app service mounted at **`/app/profiles`**.
Railway volumes are service-level resources, so create/attach the volume in the
Railway service's **Volumes** settings; the repository cannot provision a named
Railway volume by itself. The mount path must be exactly `/app/profiles` so the
API and the worker share the same browser profiles.

The image also declares `/app/profiles` as a Docker volume and creates the
mount-point during the build. If no Railway volume is attached, `start.sh`
creates the directory and the application starts with fresh profiles instead of
failing deployment. This fallback is ephemeral: the database may still say an
account is `ACTIVE` / `connected`, but the browser session will be gone after a
restart or deploy and the account must be connected again. A persistent Railway
volume is therefore strongly recommended for real use.

`railway.json` deliberately does **not** set `requiredMountPath`. That setting
would make Railway reject a deployment before the fallback can run. An attached
volume at `/app/profiles` is still used automatically when present.

Volumes may mount as **root**, while this image runs as the non-root `appuser`,
so you may also need to set `RAILWAY_RUN_UID=0` on the service — otherwise
`start.sh` exits at boot with a clear "not writable" error.

## Environment variables

### Required

| Variable | Notes |
|---|---|
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}`. A plain `postgresql://` URL is auto-rewritten to `postgresql+asyncpg://` by `core/config.py`. |
| `REDIS_URL` | `${{Redis.REDIS_URL}}`. Celery broker/backend **and** the profile locks. |
| `JWT_SECRET` | `openssl rand -hex 32`. (Legacy alias `JWT_SECRET_KEY` also accepted.) |
| `CREDENTIAL_ENCRYPTION_KEY` | 32-byte hex — `python -c "import secrets; print(secrets.token_hex(32))"`. Validated at startup; a bad value aborts boot. (Legacy alias `ENCRYPTION_KEY`.) |
| `PASSWORD_RESET_URL` | e.g. `https://your-frontend.vercel.app/reset-password`. |
| `BACKEND_CORS_ORIGINS` | Comma-separated. **Must include your exact frontend origin** or the browser blocks connect calls before they reach FastAPI. |
| `RESEND_API_KEY` | Transactional email. |
| `FROM_EMAIL` | e.g. `noreply@yourdomain.com`. |

### Strongly recommended

| Variable | Value | Why |
|---|---|---|
| `PROFILE_STORAGE_DIR` | `/app/profiles` | Matches the persistent volume mount. The Docker image sets this default; only override it when deliberately using another storage path. |
| `ENVIRONMENT` | `production` or `deployment` | `deployment` = public demo (no Beat, no recurring jobs, banner shown). See "Optional tuning". |
| `PYTHONUNBUFFERED` | `1` | Logs appear immediately. |
| `RAILWAY_RUN_UID` | `0` when needed | Lets the runtime user write a volume created with root ownership. Not needed when the attached volume already has permissions for `appuser`. |

`PORT` is injected by Railway and honoured by `start.sh`; do not set it.

### Optional tuning

| Variable | Default | Notes |
|---|---|---|
| `RUN_WEB` / `RUN_WORKER` / `RUN_BEAT` | `1` | Disable a process (e.g. `RUN_WEB=0` for a worker-only service if you ever split them). |
| `WEB_CONCURRENCY` | `1` | **Keep at 1.** `browser_view`, `live_browser` and `session_manager` are per-process in-memory singletons — with 2+ workers QR frames and pending verification sessions land in the wrong process. |
| `CELERY_CONCURRENCY` | `1` | Each task launches a ~400–600 MB Chromium. |
| `CELERY_QUEUES` | all | e.g. `linkedin_sessions,default`. |
| `CELERY_LOGLEVEL` | `info` | |
| `WAIT_FOR_WEB_SECONDS` | `300` | How long to wait for migrations before giving up. |
| `SHUTDOWN_GRACE_SECONDS` | `25` | SIGTERM→SIGKILL window for Chromium cleanup. |
| `ADMIN_API_ENFORCED` | `false` | Flip to `true` once admin roles are assigned. |
| `WHATSAPP_FORWARD_DELAY_SECONDS` | `10` | Pacing between forwarded messages. |
| `ENVIRONMENT` | `production` | Set to `deployment` on the public demo: skips Celery Beat, refuses to arm recurring jobs, and shows the "run it locally" banner. Any other value runs the full stack. See [running_locally.md](running_locally.md). |
| `SCHEDULED_JOBS_ENABLED` | derived from `ENVIRONMENT` | Explicit override for timer-driven work. Unset = off in `deployment`, on elsewhere. |
| `SUPPORT_EMAIL` | `saifullahkhanofficial1@gmail.com` | Contact address shown in the hosted-demo banner. |
| `LINKEDIN_ENABLED` | `false` | LinkedIn automation is off by default — LinkedIn blocks sign-ins from datacenter IPs, so it needs one residential proxy per account. See [linkedin_availability.md](linkedin_availability.md). WhatsApp is unaffected. |
| `LINKEDIN_DISABLED_MESSAGE` | see `core/config.py` | User-facing copy shown while LinkedIn is disabled. |

## Resources

Give the service **at least 2 GB RAM**. A single Chromium persistent context is
~400–600 MB; the API's browser view plus a worker task can exceed 1 GB. An
OOM-kill mid-connect produces no traceback and looks exactly like
"LinkedIn/WhatsApp won't connect".

## Boot order

`start.sh` starts uvicorn first and waits for it to accept TCP connections.
Uvicorn binds only *after* the lifespan finishes `init_db()` + Alembic
migrations, so Beat and the worker never touch a half-migrated schema. If any
of the three processes dies, the container exits non-zero and Railway restarts
it — a silently dead worker is the exact failure mode where campaigns and scans
appear to do nothing.

## Known limitation: datacenter IPs

LinkedIn frequently serves a CAPTCHA/checkpoint to Railway egress IPs
(`LinkedInSessionStatus.CAPTCHA` / `CHECKPOINT`). The schema has sticky
per-account `proxy_*` columns for exactly this — assign one residential proxy
per account permanently (see `docs/persistent_profiles_rollout.md`).

## Local development is unchanged

`start.sh` is only the Docker image's `CMD`. It does not affect:

* `python main.py` / `python run_dev_server.py`
* `celery -A worker.celery_app worker` and `... beat` in separate terminals
* `docker-compose.yml` — its `api`, `worker` and `beat` services each declare
  their own `command:`, which overrides the image `CMD`.

## Troubleshooting

| Symptom | Check |
|---|---|
| Profiles reset after a deploy/restart | Attach a persistent volume at `/app/profiles`; without one the app intentionally uses fresh ephemeral profiles. |
| `FATAL: PROFILE_STORAGE_DIR ... not writable` | Set `RAILWAY_RUN_UID=0`, or fix the attached volume's ownership/permissions for `appuser`. |
| `FATAL: the API never became ready` | `DATABASE_URL` unreachable, failed migration, or bad `CREDENTIAL_ENCRYPTION_KEY` — the traceback is directly above this line. |
| Connect returns 500 immediately | `REDIS_URL` missing/unreachable (profile lock). |
| Connect works, campaigns/scans do nothing | Worker down — `GET /api/v1/system/queues/celery-inspect`. |
| Connected, then logged out after deploy | The persistent volume is missing, mounted at the wrong path, or not writable. Attach it at `/app/profiles`. |
| QR code never appears / stuck | `WEB_CONCURRENCY` or `numReplicas` > 1. |
| CORS errors in the browser console | `BACKEND_CORS_ORIGINS` missing the frontend origin. |
| Connect dies with no logs | OOM — raise memory. |

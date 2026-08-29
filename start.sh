#!/usr/bin/env bash
#
# Container entrypoint — runs the API, the Celery worker and Celery Beat as
# three processes inside ONE container.
#
# FILE: start.sh
#
# Why one container?
# ------------------
# On Railway a volume can be attached to exactly one service, and all three
# processes need the SAME durable Chromium profile directory
# (PROFILE_STORAGE_DIR): the API owns the browser during LinkedIn/WhatsApp
# *connect*, and the worker reopens those same profiles for campaign sessions,
# feed scrolls and WhatsApp scans. Splitting them into separate Railway
# services would give each its own filesystem, so the worker would open an
# empty WhatsApp profile and see a QR code instead of the live session.
# The Redis locks (worker/profile_lock.py) already serialize access across
# processes, so co-locating them is safe.
#
# This script is ONLY used by the Docker image's CMD. It does not affect:
#   * local `python main.py` / `run_dev_server.py`
#   * local `celery -A worker.celery_app worker|beat` in separate terminals
#   * docker-compose.yml — its api/worker/beat services each declare their own
#     `command:`, which overrides the image CMD.
#
# Behaviour
# ---------
#   * Verifies the configuration the app cannot start without (DATABASE_URL,
#     REDIS_URL, CREDENTIAL_ENCRYPTION_KEY) and exits with a one-line
#     diagnosis naming the missing variable. Without this, a missing Railway
#     variable killed uvicorn at import time — before it bound $PORT — and the
#     only symptom was a generic "Network › Healthcheck" failure ~5 min later.
#   * Makes sure the profile storage is writable, falling back to
#     PROFILE_FALLBACK_DIR instead of aborting the deploy when a root-owned
#     Railway volume shadows the mount point (see the preflight below).
#   * Starts uvicorn first and waits until it is accepting TCP connections.
#     Uvicorn binds its socket only AFTER the lifespan startup (init_db +
#     Alembic migrations) completes, so this guarantees the worker never
#     queries a half-migrated schema.
#   * Starts Beat and the worker afterwards.
#   * Propagates SIGTERM/SIGINT to every child and waits for them to exit, so
#     a Railway redeploy/restart shuts Chromium down cleanly instead of
#     leaving Chromium SingletonLock files and held Redis profile locks
#     behind.
#   * If ANY process dies, the whole container exits non-zero so the platform
#     restarts it — a silently-dead worker is the exact failure mode where
#     campaigns/scans appear to "do nothing".
#
# Toggles (env vars)
# ------------------
#   RUN_WEB=1|0                 run uvicorn                (default 1)
#   RUN_WORKER=1|0              run the Celery worker      (default 1)
#   RUN_BEAT=1|0                run Celery Beat            (default 1)
#   PORT                        uvicorn port               (default 8000)
#   WEB_CONCURRENCY             uvicorn workers            (default 1 — MUST
#                               stay 1: browser_view / live_browser /
#                               session_manager are in-memory singletons)
#   CELERY_CONCURRENCY          worker processes           (default 1)
#   CELERY_QUEUES               queues to consume          (default all)
#   CELERY_LOGLEVEL             worker/beat log level      (default info)
#   WAIT_FOR_WEB_SECONDS        API readiness timeout      (default 300)
#   SHUTDOWN_GRACE_SECONDS      SIGTERM grace before KILL  (default 25)
#   PROFILE_FALLBACK_DIR        where to keep profiles when the configured
#                               PROFILE_STORAGE_DIR is not writable
#                                                          (default
#                                                           /tmp/linkeasy-profiles)
#
set -uo pipefail

log() { printf '[start.sh] %s\n' "$*"; }

# ── Configuration ────────────────────────────────────────────────────────────

# Capture whether the operator set RUN_BEAT explicitly BEFORE applying the
# default below — once it is defaulted we can no longer tell the difference.
if [ -n "${RUN_BEAT+x}" ]; then
    RUN_BEAT_WAS_EXPLICIT=1
else
    RUN_BEAT_WAS_EXPLICIT=0
fi

RUN_WEB="${RUN_WEB:-1}"
RUN_WORKER="${RUN_WORKER:-1}"
RUN_BEAT="${RUN_BEAT:-1}"

# ── Hosted-demo (ENVIRONMENT=deployment) ─────────────────────────────────────
# On the public demo we do not run Celery Beat. Beat's three dispatchers fire
# every 60s and each one can wake a ~500 MB Chromium with nobody watching; in
# a free-tier container that also serves the API that means OOM-kills and
# half-finished browser sessions. The Celery WORKER still runs, so everything
# the user actually clicks — manual WhatsApp scans, connects, live chat —
# still works. Only the unattended timers are removed.
#
# The backend enforces the same rule independently (settings.
# scheduled_jobs_enabled clears beat_schedule and gates the "activate"
# endpoints), so this is defence in depth, not the only guard.
#
# An explicit RUN_BEAT=1 still wins, for debugging the demo with schedules on.
ENVIRONMENT_LOWER="$(printf '%s' "${ENVIRONMENT:-}" | tr '[:upper:]' '[:lower:]')"
IS_DEPLOYMENT=0
if [ "$ENVIRONMENT_LOWER" = "deployment" ]; then
    IS_DEPLOYMENT=1
    if [ "$RUN_BEAT_WAS_EXPLICIT" = "0" ]; then
        RUN_BEAT=0
    fi
fi

PORT="${PORT:-8000}"
WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
CELERY_CONCURRENCY="${CELERY_CONCURRENCY:-1}"
CELERY_LOGLEVEL="${CELERY_LOGLEVEL:-info}"
CELERY_QUEUES="${CELERY_QUEUES:-}"
WAIT_FOR_WEB_SECONDS="${WAIT_FOR_WEB_SECONDS:-300}"
SHUTDOWN_GRACE_SECONDS="${SHUTDOWN_GRACE_SECONDS:-25}"

# Beat must never write its schedule database into the image/repo directory:
# it is read-only in spirit and wiped on every deploy. /tmp is always writable.
export CELERY_BEAT_SCHEDULE_FILE="${CELERY_BEAT_SCHEDULE_FILE:-/tmp/linkeasy-celerybeat-schedule}"

# Durable Chromium profiles. Railway can mount a persistent volume at this
# exact path. If no volume is attached, the Dockerfile provides the directory
# (created during the image build; no VOLUME instruction is declared —
# Railway's builder rejects it) and this preflight creates it when the image
# is run directly or the directory is missing. The app can therefore start
# with fresh, ephemeral profiles instead of failing the build/deploy;
# existing sessions will need to be connected again after a restart or
# deploy.
PROFILE_STORAGE_DIR="${PROFILE_STORAGE_DIR:-/app/profiles}"
# Used only when the configured directory exists but cannot be written (the
# usual cause: a Railway volume mounted over it, owned by root, while this
# image runs as the non-root `appuser`). Overridable for testing.
PROFILE_FALLBACK_DIR="${PROFILE_FALLBACK_DIR:-/tmp/linkeasy-profiles}"
export PROFILE_STORAGE_DIR

# ── Preflight: required configuration ────────────────────────────────────────
#
# Why this exists: core/config.py builds `settings` at IMPORT time, and
# core.security.validate_encryption_key() runs in the FastAPI lifespan. A
# missing variable therefore killed uvicorn before it ever bound $PORT, and
# the platform's only symptom was a generic "Network › Healthcheck" failure
# five minutes later. Checking here turns that into one explicit line naming
# the variable, in the first second of the deploy log.

# True when $1 is set in the environment OR defined in a local .env file
# (start.sh is also runnable outside the image, where .env is how config is
# supplied; the Docker build excludes .env, so on the platform only the
# environment matters).
env_value_present() {
    local name="$1"
    if [ -n "${!name+x}" ]; then
        return 0
    fi
    if [ -f .env ] && grep -qE "^[[:space:]]*(export[[:space:]]+)?${name}=" .env 2>/dev/null; then
        return 0
    fi
    return 1
}

# True when ANY of the given names (a variable and its legacy aliases) is set.
any_value_present() {
    local name
    for name in "$@"; do
        if env_value_present "$name"; then
            return 0
        fi
    done
    return 1
}

MISSING_REQUIRED=""
# DATABASE_URL — database.py builds the engine from it at import time.
any_value_present DATABASE_URL || MISSING_REQUIRED="$MISSING_REQUIRED DATABASE_URL"
# REDIS_URL — Celery broker/backend AND the per-account profile locks, so a
# missing value breaks every connect path with a confusing 500.
any_value_present REDIS_URL || MISSING_REQUIRED="$MISSING_REQUIRED REDIS_URL"
# CREDENTIAL_ENCRYPTION_KEY — validated during lifespan startup; a missing or
# malformed key aborts boot (ENCRYPTION_KEY is the accepted legacy alias).
any_value_present CREDENTIAL_ENCRYPTION_KEY ENCRYPTION_KEY \
    || MISSING_REQUIRED="$MISSING_REQUIRED CREDENTIAL_ENCRYPTION_KEY"

if [ -n "$MISSING_REQUIRED" ]; then
    log "FATAL: required configuration is missing:$MISSING_REQUIRED"
    log "       The API cannot bind \$PORT without these, so the deploy would"
    log "       otherwise sit in 'Healthcheck' for ${WAIT_FOR_WEB_SECONDS}s and fail"
    log "       with no explanation. Set them on the Railway service's Variables"
    log "       tab, e.g.:"
    log "         DATABASE_URL=\${{Postgres.DATABASE_URL}}"
    log "         REDIS_URL=\${{Redis.REDIS_URL}}"
    log "         CREDENTIAL_ENCRYPTION_KEY=\$(python -c 'import secrets; print(secrets.token_hex(32))')"
    exit 1
fi

# These no longer abort boot (core/config.py defaults them to ""), but each
# one silently disables a user-visible feature, so say so up front.
MISSING_OPTIONAL=""
any_value_present JWT_SECRET JWT_SECRET_KEY \
    || MISSING_OPTIONAL="$MISSING_OPTIONAL JWT_SECRET"
env_value_present BACKEND_CORS_ORIGINS || MISSING_OPTIONAL="$MISSING_OPTIONAL BACKEND_CORS_ORIGINS"
env_value_present PASSWORD_RESET_URL || MISSING_OPTIONAL="$MISSING_OPTIONAL PASSWORD_RESET_URL"
env_value_present RESEND_API_KEY || MISSING_OPTIONAL="$MISSING_OPTIONAL RESEND_API_KEY"
env_value_present FROM_EMAIL || MISSING_OPTIONAL="$MISSING_OPTIONAL FROM_EMAIL"
if [ -n "$MISSING_OPTIONAL" ]; then
    log "WARN: optional configuration missing:$MISSING_OPTIONAL"
    log "      The service still starts, but: JWT_SECRET unset = login tokens"
    log "      cannot be signed/verified; BACKEND_CORS_ORIGINS unset = browsers"
    log "      block every call from the frontend; the remaining three disable"
    log "      password-reset email."
fi

# ── Preflight: profile storage ───────────────────────────────────────────────

# Can this uid actually create files in $1? mkdir -p succeeds on an existing
# root-owned directory, so the write test is the part that matters.
profile_dir_writable() {
    mkdir -p "$1" 2>/dev/null || return 1
    touch "$1/.writable" 2>/dev/null || return 1
    rm -f "$1/.writable" 2>/dev/null || true
    return 0
}

PROFILE_STORAGE_FALLBACK=0
PROFILE_STORAGE_MISSING=0
if [ ! -d "$PROFILE_STORAGE_DIR" ]; then
    PROFILE_STORAGE_MISSING=1
fi

if ! profile_dir_writable "$PROFILE_STORAGE_DIR" && [ "$(id -u)" = "0" ]; then
    # Running as root (e.g. RAILWAY_RUN_UID=0) — a freshly mounted volume is
    # owned by root, so we can just take it over.
    chown -R "$(id -u):$(id -g)" "$PROFILE_STORAGE_DIR" 2>/dev/null || true
fi

if ! profile_dir_writable "$PROFILE_STORAGE_DIR"; then
    # Railway mounts volumes as root:root, which shadows the appuser-owned
    # mount point baked into the image; no chown/chmod done at build time can
    # survive that. Rather than abort the whole deploy over a permissions
    # problem — which surfaced only as "Healthcheck failure" — fall back to a
    # writable directory and boot. Profiles then do not survive a restart, so
    # the operator is told exactly which variable restores persistence.
    if profile_dir_writable "$PROFILE_FALLBACK_DIR"; then
        log "WARN: PROFILE_STORAGE_DIR=$PROFILE_STORAGE_DIR is not writable by uid $(id -u)."
        log "      A Railway volume mounted there is owned by root, while this"
        log "      image runs as the non-root 'appuser'."
        log "      Falling back to $PROFILE_FALLBACK_DIR so the service can start."
        log "      >>> Browser profiles will NOT persist across restarts/deploys. <<<"
        log "      To use the persistent volume, set RAILWAY_RUN_UID=0 on the"
        log "      Railway service and redeploy (that makes the container run as"
        log "      root, which owns the mount)."
        PROFILE_STORAGE_DIR="$PROFILE_FALLBACK_DIR"
        PROFILE_STORAGE_FALLBACK=1
    else
        log "FATAL: neither $PROFILE_STORAGE_DIR nor $PROFILE_FALLBACK_DIR is"
        log "       writable by uid $(id -u) — there is nowhere to put a browser"
        log "       profile. On Railway set RAILWAY_RUN_UID=0 on the service."
        exit 1
    fi
fi

export PROFILE_STORAGE_DIR
if ! chmod 700 "$PROFILE_STORAGE_DIR" 2>/dev/null; then
    log "WARN: could not chmod 700 $PROFILE_STORAGE_DIR (continuing)"
fi

log "profiles     : $PROFILE_STORAGE_DIR (uid=$(id -u))"
if [ "$PROFILE_STORAGE_FALLBACK" = "1" ]; then
    log "              (ephemeral fallback — see the warning above)"
elif [ "$PROFILE_STORAGE_MISSING" = "1" ]; then
    log "WARN: no profile volume/directory was present; fresh profiles will be"
    log "      created here and will be ephemeral unless a persistent volume is"
    log "      mounted at $PROFILE_STORAGE_DIR."
fi
log "beat schedule: $CELERY_BEAT_SCHEDULE_FILE"
log "processes    : web=$RUN_WEB worker=$RUN_WORKER beat=$RUN_BEAT"

if [ "$IS_DEPLOYMENT" = "1" ]; then
    log "ENVIRONMENT=deployment — hosted demo mode:"
    if [ "$RUN_BEAT" = "1" ]; then
        log "  * Beat is running because RUN_BEAT was set explicitly."
    else
        log "  * Celery Beat is DISABLED: no scheduled campaign steps, no"
        log "    recurring feed/WhatsApp scans. Set RUN_BEAT=1 to override."
    fi
    log "  * The Celery worker still runs, so on-demand actions (manual scans,"
    log "    connects, live chat) work normally."
    log "  * LinkEasy is intended to run locally for the full feature set."
fi

if [ "$RUN_WEB" != "1" ] && [ "$RUN_WORKER" != "1" ] && [ "$RUN_BEAT" != "1" ]; then
    log "FATAL: RUN_WEB, RUN_WORKER and RUN_BEAT are all disabled — nothing to run."
    exit 1
fi

# ── Child process bookkeeping ────────────────────────────────────────────────

declare -a CHILD_PIDS=()
declare -a CHILD_NAMES=()
SHUTTING_DOWN=0

register_child() {
    CHILD_NAMES+=("$1")
    CHILD_PIDS+=("$2")
    log "started $1 (pid $2)"
}

# Forward SIGTERM/SIGINT to every child, then wait out a grace period before
# SIGKILL. Chromium needs this window to release its SingletonLock, and the
# Celery worker needs it to finish/requeue the task it is holding.
shutdown_children() {
    if [ "$SHUTTING_DOWN" = "1" ]; then
        return
    fi
    SHUTTING_DOWN=1
    log "received shutdown signal — stopping children"

    local i
    for i in "${!CHILD_PIDS[@]}"; do
        if kill -0 "${CHILD_PIDS[$i]}" 2>/dev/null; then
            log "  SIGTERM -> ${CHILD_NAMES[$i]} (pid ${CHILD_PIDS[$i]})"
            kill -TERM "${CHILD_PIDS[$i]}" 2>/dev/null || true
        fi
    done

    local waited=0
    while [ "$waited" -lt "$SHUTDOWN_GRACE_SECONDS" ]; do
        local alive=0
        for i in "${!CHILD_PIDS[@]}"; do
            if kill -0 "${CHILD_PIDS[$i]}" 2>/dev/null; then
                alive=1
                break
            fi
        done
        [ "$alive" = "0" ] && break
        sleep 1
        waited=$((waited + 1))
    done
    log "children stopped after ${waited}s (grace ${SHUTDOWN_GRACE_SECONDS}s)"

    for i in "${!CHILD_PIDS[@]}"; do
        if kill -0 "${CHILD_PIDS[$i]}" 2>/dev/null; then
            log "  SIGKILL -> ${CHILD_NAMES[$i]} (pid ${CHILD_PIDS[$i]}) after ${SHUTDOWN_GRACE_SECONDS}s"
            kill -KILL "${CHILD_PIDS[$i]}" 2>/dev/null || true
        fi
    done

    log "all children stopped"
}

trap 'shutdown_children; exit 0' TERM INT

# ── Start the API ────────────────────────────────────────────────────────────

if [ "$RUN_WEB" = "1" ]; then
    # NOTE: --workers must stay 1. The embedded browser view, the WhatsApp /
    # LinkedIn live-chat managers and the pending-verification session manager
    # are per-process in-memory singletons; with 2+ workers the QR frames and
    # the pending login session would live in a different process than the one
    # the frontend's next poll lands on.
    uvicorn main:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        --workers "$WEB_CONCURRENCY" \
        --log-level info \
        --timeout-graceful-shutdown 20 &
    register_child "api" "$!"

    # Wait for the socket: uvicorn binds AFTER the lifespan runs init_db() and
    # the Alembic migrations, so this is our "schema is ready" signal.
    log "waiting up to ${WAIT_FOR_WEB_SECONDS}s for the API to accept connections on :$PORT"
    if ! python - "$PORT" "$WAIT_FOR_WEB_SECONDS" <<'PY'
import socket, sys, time

port = int(sys.argv[1])
deadline = time.monotonic() + float(sys.argv[2])
while time.monotonic() < deadline:
    with socket.socket() as s:
        s.settimeout(2)
        try:
            s.connect(("127.0.0.1", port))
        except OSError:
            time.sleep(1)
            continue
    sys.exit(0)
sys.exit(1)
PY
    then
        log "FATAL: the API never became ready — see the traceback above."
        log "       Most common causes: DATABASE_URL unreachable, a failed"
        log "       Alembic migration, or a missing CREDENTIAL_ENCRYPTION_KEY."
        shutdown_children
        exit 1
    fi
    log "API is ready — database migrations completed"
fi

# ── Start Celery Beat ────────────────────────────────────────────────────────

if [ "$RUN_BEAT" = "1" ]; then
    # Beat only PUBLISHES the three dispatch tasks every 60s; it opens no
    # browser and needs no volume access of its own.
    celery -A worker.celery_app beat \
        --loglevel="$CELERY_LOGLEVEL" \
        --schedule="$CELERY_BEAT_SCHEDULE_FILE" &
    register_child "beat" "$!"
fi

# ── Start the Celery worker ──────────────────────────────────────────────────

if [ "$RUN_WORKER" = "1" ]; then
    # Concurrency defaults to 1: every automation task launches a Chromium
    # persistent context (~400-600 MB). Two of them plus the API's own browser
    # view will OOM a small Railway instance, and an OOM-kill mid-connect looks
    # exactly like "LinkedIn/WhatsApp won't connect".
    worker_args=(
        -A worker.celery_app worker
        --loglevel="$CELERY_LOGLEVEL"
        --concurrency="$CELERY_CONCURRENCY"
        --pool=prefork
        # Distinct node name so the worker and any future replica do not
        # collide in `celery inspect` (surfaced by /api/v1/system/queues).
        --hostname="worker@%h"
    )
    if [ -n "$CELERY_QUEUES" ]; then
        worker_args+=(--queues="$CELERY_QUEUES")
    fi
    celery "${worker_args[@]}" &
    register_child "worker" "$!"
fi

# ── Supervise ────────────────────────────────────────────────────────────────

log "all processes up — supervising"

while true; do
    for i in "${!CHILD_PIDS[@]}"; do
        pid="${CHILD_PIDS[$i]}"
        name="${CHILD_NAMES[$i]}"
        if ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" 2>/dev/null
            code=$?
            log "FATAL: '$name' (pid $pid) exited with code $code — shutting the container down so the platform restarts it"
            shutdown_children
            exit "$code"
        fi
    done
    sleep 2
done

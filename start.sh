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
export PROFILE_STORAGE_DIR

# ── Preflight ────────────────────────────────────────────────────────────────

PROFILE_STORAGE_FALLBACK=0
if [ ! -d "$PROFILE_STORAGE_DIR" ]; then
    PROFILE_STORAGE_FALLBACK=1
fi
if ! mkdir -p "$PROFILE_STORAGE_DIR" 2>/dev/null; then
    log "FATAL: cannot create PROFILE_STORAGE_DIR=$PROFILE_STORAGE_DIR"
    log "       On Railway, a mounted volume may be owned by root; set"
    log "       RAILWAY_RUN_UID=0 on the service or pre-create the directory"
    log "       with write permission for the container user."
    exit 1
fi
if ! chmod 700 "$PROFILE_STORAGE_DIR" 2>/dev/null; then
    log "WARN: could not chmod 700 $PROFILE_STORAGE_DIR (continuing)"
fi
if ! touch "$PROFILE_STORAGE_DIR/.writable" 2>/dev/null; then
    log "FATAL: PROFILE_STORAGE_DIR=$PROFILE_STORAGE_DIR is not writable by uid $(id -u)."
    log "       On Railway, a mounted volume may be owned by root; set"
    log "       RAILWAY_RUN_UID=0 on the service or fix the volume permissions."
    exit 1
fi
rm -f "$PROFILE_STORAGE_DIR/.writable" 2>/dev/null || true

log "profiles     : $PROFILE_STORAGE_DIR (uid=$(id -u))"
if [ "$PROFILE_STORAGE_FALLBACK" = "1" ]; then
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

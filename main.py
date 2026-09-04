import asyncio
import logging
import os
import time

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from api.dependencies import (
    get_db,
    require_roles,
    SCHEDULED_JOBS_DISABLED_DETAIL,
)
from api.v1.auth import router as auth_router
from api.v1.linkedin import router as linkedin_router
from api.v1.users import router as users_router
from api.v1.campaigns import router as campaign_router
from api.v1.leads import router as leads_router
from api.v1.feed_leads import router as feed_leads_router

from api.v1.test_automation import router as test_automation_router
from api.v1.feed_scroll import router as feed_scroll_router
from api.v1.whatsapp_scanner import router as whatsapp_scanner_router
from api.v1.whatsapp_live import router as whatsapp_live_router
from api.v1.linkedin_live import router as linkedin_live_router
from api.v1.linkedin_live import stop_router as linkedin_live_stop_router
from api.v1.linkedin_profile import router as linkedin_profile_router
from api.v1.live import router as live_router
from api.v1.system_queues import router as system_queues_router
from api.v1.admin import router as admin_router
from api.v1.social_scheduler import router as social_scheduler_router, UPLOADS_URL_PREFIX
from api.v1.user_data import router as user_data_router
from core.config import settings
from core.logging_config import get_logger
try:
    from core.logging_config import reset_logging
except ImportError:  # lightweight test doubles may only expose get_logger
    def reset_logging():
        return None
from core.security import validate_encryption_key
from database import init_db
from models.roles import UserRole
from api.dependencies import get_current_user
from models.user import User
from automation.session_manager import start_periodic_cleanup
from run_migrations import run_migrations

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run database initializations on startup, start background tasks.

    Every startup step is wrapped so that the *reason* for a shutdown is
    always visible in the logs.  Previously an exception inside
    ``init_db`` / ``run_migrations`` would bubble out of the lifespan and
    Uvicorn would simply stop the server after the Alembic
    ``Will assume transactional DDL.`` line — leaving the user with no
    traceback and the impression that the service “shut itself down”.
    """
    # Fail loudly at startup if the credential encryption key is missing or
    # malformed — a config mistake must not silently corrupt/expose secrets.
    try:
        validate_encryption_key()
    except Exception:
        logger.exception("Startup aborted: CREDENTIAL_ENCRYPTION_KEY is missing or malformed")
        raise

    # Report every unset-but-important variable in the first lines of the
    # deploy log. These no longer abort startup (see core/config.py), so
    # without this they would only show up later as a broken feature.
    for _name, _effect in settings.missing_optional_settings().items():
        logger.warning("Configuration missing: %s is not set — %s", _name, _effect)

    # Instagram Reels are uploaded by the app to its own storage, so the
    # default direct (resumable) upload never needs a public URL. If an
    # operator has disabled direct upload AND there is no public address, every
    # scheduled Instagram post is guaranteed to fail — say so at boot instead
    # of inside a failed publish hours later.
    if not settings.INSTAGRAM_DIRECT_UPLOAD:
        from services.social.instagram import is_public_video_url

        if not is_public_video_url(settings.PUBLIC_API_URL):
            logger.warning(
                "Configuration warning: INSTAGRAM_DIRECT_UPLOAD is disabled and PUBLIC_API_URL is "
                "not a publicly reachable address, so Instagram Reels cannot be published. Re-enable "
                "INSTAGRAM_DIRECT_UPLOAD (videos are already stored on this server) or set "
                "PUBLIC_API_URL to a public URL."
            )

    # Ensure tables exist, then apply pending schema migrations.  This project
    # does not have Alembic revisions for the original/base tables, so a brand
    # new database still needs create_all() first.  create_all() never adds new
    # columns to existing tables, though, so we immediately run the idempotent
    # migrations afterward (e.g. feed_scroll_results.post_url and the feed-scroll
    # tables for older deployments).  Alembic's runner uses asyncio.run(), so
    # execute it in a worker thread from FastAPI's already-running event loop.
    try:
        await init_db()
        logger.info("Database init (create_all) completed")
    except Exception as exc:
        # One concise line instead of logger.exception: init_db() already
        # raises a short RuntimeError with a human diagnosis, and uvicorn
        # re-prints every lifespan failure to stderr. Duplicating the raw
        # ~70-frame asyncpg traceback on both streams every ~2s on a
        # crash-looping deployment exceeded Railway's 500 logs/sec rate
        # limit, which silently dropped the log lines that mattered.
        logger.error("Startup aborted: init_db() failed: %s", exc)
        raise

    logger.info("Running database migrations on startup...")
    try:
        # run_migrations() internally calls asyncio.run(), which must NOT run
        # inside the main event loop thread on Windows (ProactorEventLoop).
        # asyncio.to_thread() moves it to a worker thread where no loop is
        # running, so asyncio.run() can create its own loop safely.
        await asyncio.to_thread(run_migrations)
        # Alembic's env.py historically called logging.config.fileConfig which
        # wiped the root handler and set level to WARNING, hiding INFO logs.
        # We now preserve existing loggers in env.py, but as a safety net we
        # force-reinstall our flushing stdout handler here. Without this, the
        # user sees "Running database migrations..." and then silence,
        # thinking the service shut itself down.
        try:
            reset_logging()
        except Exception:
            # Logging fix is best-effort; don't fail startup if it errors.
            pass
        logger.info("Database migrations completed")
    except Exception:
        # Ensure logs remain visible even when migrations fail, so traceback shows.
        try:
            reset_logging()
        except Exception:
            pass
        # Migrations are idempotent and the base tables already exist via
        # init_db(), so the API can still serve traffic even if a migration
        # fails.  Log the full traceback loudly, then re-raise so the
        # operator notices — or comment out the `raise` to allow degraded
        # startup.
        logger.exception(
            "Database migrations failed — the service will not start. "
            "Fix the migration error or set SKIP_MIGRATIONS=1 to start without migrating"
        )
        raise

    cleanup_task = start_periodic_cleanup(interval_seconds=300, timeout_minutes=15)

    logger.info("Service startup complete — application is ready")
    # Extra visibility: confirm logging is still working after lifespan yield point is near.
    print("Service startup complete — application is ready", flush=True)
    try:
        yield
    finally:
        logger.info("Shutting down — cancelling periodic cleanup task")
        try:
            cleanup_task.cancel()
            await cleanup_task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error while cancelling cleanup task")

        # Stop every embedded browser view (one per WhatsApp session) so no
        # orphan Chromium lingers.
        try:
            from services.browser_view import all_browser_views

            for browser_view in all_browser_views():
                try:
                    await browser_view.stop()
                except Exception:
                    logger.exception("Error while stopping browser view")
        except Exception:
            logger.exception("Error while stopping browser view")

        # Same for the dedicated live-chat browsers — releasing each
        # session's profile lock early lets the Celery scan task pick up
        # where it left off.
        try:
            from services.whatsapp_live_browser import all_live_browsers

            for live_browser in all_live_browsers():
                try:
                    await live_browser.stop()
                except Exception:
                    logger.exception("Error while stopping live chat browser")
        except Exception:
            logger.exception("Error while stopping live chat browser")

        # LinkedIn live browser too — same profile-lock pattern.
        try:
            from services.linkedin_live_browser import linkedin_live_browser

            await linkedin_live_browser.stop()
        except Exception:
            logger.exception("Error while stopping LinkedIn live browser")


def _request_user_email(request: Request) -> str | None:
    """Best-effort email of the caller for the API log stream (no DB hit)."""
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jose_jwt.decode(
            auth[7:].strip(),
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload.get("sub")
    except Exception:
        return None


app = FastAPI(title="LinkeFlow Authentication API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_api_calls(request: Request, call_next):
    """Log every API call to the terminal (backend logs).

    In production, logs go to the terminal/backend for easy monitoring.
    Live-stream endpoints are excluded to prevent feedback loops.
    """
    path = request.url.path
    # Skip the live-stream endpoints (feedback loops) and the platform
    # healthcheck. Railway probes the healthcheck path every few seconds, so
    # logging each hit buries the lines that matter — and this codebase has
    # already been bitten by Railway's log rate limit dropping the traceback
    # that explained a crash-looping deploy.
    if path.startswith("/api/v1/live") or path == "/health":
        return await call_next(request)

    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    # Log to terminal instead of frontend
    user = _request_user_email(request)
    logger.info(
        "API call: %s %s%s -> %d (%s ms)%s",
        request.method,
        path,
        f"?{request.url.query[:160]}" if request.url.query else "",
        response.status_code,
        f"{duration_ms:.1f}",
        f" user={user}" if user else "",
    )
    return response


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(linkedin_router)
app.include_router(campaign_router)
app.include_router(leads_router)
app.include_router(feed_leads_router)
app.include_router(test_automation_router)
app.include_router(feed_scroll_router)
app.include_router(whatsapp_scanner_router)
app.include_router(whatsapp_live_router)
app.include_router(linkedin_live_router)
# Ungated /live/stop — always available so a running browser can be shut down
# and its profile lock released even while LinkedIn is disabled.
app.include_router(linkedin_live_stop_router)
app.include_router(linkedin_profile_router)
app.include_router(live_router)
app.include_router(system_queues_router)
app.include_router(admin_router)
app.include_router(social_scheduler_router)
# Public account-deletion endpoints (Meta User Data Deletion requirement):
# email-confirmed, rate-limited, no account enumeration. Registered last so
# every /api/v1/user-data/* path is served exactly by this router.
app.include_router(user_data_router)

# Uploaded videos for the social scheduler. Instagram's Graph API downloads
# the video from a public URL rather than accepting an upload, so the files
# must be reachable at <PUBLIC_API_URL>/uploads/social/<generated-name>.
# Names are server-generated UUIDs (see api/v1/social_scheduler.py), so
# nothing here is guessable; the directory is created up-front because
# StaticFiles refuses to mount a missing path.
try:
    from fastapi.staticfiles import StaticFiles

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    app.mount(UPLOADS_URL_PREFIX, StaticFiles(directory=settings.UPLOAD_DIR), name="social-uploads")
except Exception as _exc:  # a read-only filesystem must not stop the API
    logger.warning("Social uploads directory %s is not mountable: %s", settings.UPLOAD_DIR, _exc)


_BOOT_MONOTONIC = time.monotonic()


@app.get("/")
async def root():
    return {"message": "LinkeFlow auth service is running"}


@app.get("/health")
async def health():
    """Liveness probe — this is Railway's ``healthcheckPath``.

    Deliberately touches no database, cache, queue or browser. Its only job is
    to prove the process is alive and its event loop is serving, so a slow or
    briefly-unavailable Postgres can never fail a deploy by itself.

    It also reports the two things that have actually broken deployments here
    and are otherwise only visible by scrolling the deploy log:
      * whether the browser-profile storage is writable (a Railway volume is
        mounted root-owned, which shadows the appuser-owned mount point in the
        image — start.sh then falls back to /tmp and profiles stop persisting);
      * which important-but-optional settings are unset.
    """
    profile_dir = settings.PROFILE_STORAGE_DIR
    try:
        profile_writable = os.access(profile_dir, os.W_OK)
    except Exception:  # a malformed path should not fail the probe
        profile_writable = False

    missing = settings.missing_optional_settings()
    return {
        "status": "ok",
        "service": "link-easy",
        "environment": settings.ENVIRONMENT,
        "uptime_seconds": round(time.monotonic() - _BOOT_MONOTONIC, 1),
        "profile_storage": {
            "path": profile_dir,
            "writable": profile_writable,
        },
        "missing_configuration": sorted(missing),
    }


@app.get("/api/v1/features")
async def feature_flags():
    """Which optional features this deployment offers.

    Unauthenticated on purpose: the frontend reads it before/independently of
    login to decide whether to render the LinkedIn surfaces at all, and it
    exposes nothing sensitive. Serving the flag from the backend (rather than
    a build-time env var) means enabling LinkedIn is a restart, not a
    frontend redeploy.
    """
    return {
        "linkedin": {
            "enabled": settings.LINKEDIN_ENABLED,
            "message": None if settings.LINKEDIN_ENABLED else settings.LINKEDIN_DISABLED_MESSAGE,
        },
        "whatsapp": {
            "enabled": True,
            "message": None,
            "scheduled_scans_enabled": settings.whatsapp_scheduled_jobs_enabled,
        },
        # Overall Beat availability. On deployment Beat remains enabled for
        # social uploads, while LinkedIn and recurring WhatsApp/feed dispatch
        # entries are omitted to protect Celery capacity.
        "scheduled_jobs": {
            "enabled": settings.scheduled_jobs_enabled,
            "message": None if settings.scheduled_jobs_enabled
            else SCHEDULED_JOBS_DISABLED_DETAIL,
        },
        # Hosted-instance banner. `notice` is null outside deployment mode,
        # which is what the frontend keys off — the banner is shown ONLY when
        # ENVIRONMENT=deployment.
        "deployment": {
            "is_demo": settings.is_deployment,
            "notice": settings.deployment_notice,
            "support_email": settings.SUPPORT_EMAIL,
        },
    }


@app.get("/db-check")
async def db_check(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
):
    """Authenticated endpoint to check database connectivity."""
    try:
        # Perform a simple query to check the connection
        await db.execute(text("SELECT 1"))
        return {"status": "ok", "message": "Database connection is healthy."}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection error",
        )

if __name__ == "__main__":
    import logging
    import uvicorn

    # Ensure startup errors are always visible, even if the user runs
    # `python main.py` without --log-level.  Uvicorn's default INFO level
    # hides lifespan tracebacks; we force it to show them.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")

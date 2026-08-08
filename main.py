import asyncio

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from api.dependencies import get_db, require_roles
from api.v1.auth import router as auth_router
from api.v1.linkedin import router as linkedin_router
from api.v1.users import router as users_router
from api.v1.campaigns import router as campaign_router
from api.v1.leads import router as leads_router
from api.v1.feed_leads import router as feed_leads_router

from api.v1.test_automation import router as test_automation_router
from api.v1.feed_scroll import router as feed_scroll_router
from api.v1.whatsapp_scanner import router as whatsapp_scanner_router
from core.config import settings
from core.logging_config import get_logger
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
    except Exception:
        logger.exception("Startup aborted: init_db() failed — check DATABASE_URL and that Postgres is reachable")
        raise

    logger.info("Running database migrations on startup...")
    try:
        # run_migrations() internally calls asyncio.run(), which must NOT run
        # inside the main event loop thread on Windows (ProactorEventLoop).
        # asyncio.to_thread() moves it to a worker thread where no loop is
        # running, so asyncio.run() can create its own loop safely.
        await asyncio.to_thread(run_migrations)
        logger.info("Database migrations completed")
    except Exception:
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


app = FastAPI(title="LinkeFlow Authentication API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router)
app.include_router(users_router)
app.include_router(linkedin_router)
app.include_router(campaign_router)
app.include_router(leads_router)
app.include_router(feed_leads_router)
app.include_router(test_automation_router)
app.include_router(feed_scroll_router)
app.include_router(whatsapp_scanner_router)


@app.get("/")
async def root():
    return {"message": "LinkeFlow auth service is running"}


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
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

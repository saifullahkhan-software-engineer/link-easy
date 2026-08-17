"""
Development launcher for the FastAPI app.

FILE: run_dev_server.py

Boots uvicorn against the Live preview / dev sandbox so the WhatsApp Live
Chat page has a real backend to hit via the vite ``/api`` proxy.

The regular lifespan (`init_db` -> `run_migrations`) tries to connect to
Postgres + Alembic; in the sandbox we don't have those, so this script
*replaces* those two functions with no-ops before the app starts. The
routers still load, schemas still validate, the live-chat manager inits
its internal state — every endpoint that doesn't actually open a DB
connection responds normally. Endpoints that need a real DB simply
return their usual 4xx/5xx.

Usage:
    .venv/bin/python run_dev_server.py --host 0.0.0.0 --port 8000
"""
import argparse
import asyncio
import os

# Configure before importing anything that builds pydantic Settings.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault(
    "CREDENTIAL_ENCRYPTION_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")


async def _async_noop(*args, **kwargs):
    """Async stand-in for ``init_db`` — main.py does ``await init_db()``."""
    return None


def _sync_noop(*args, **kwargs):
    """Sync stand-in for ``run_migrations`` — main.py wraps it in ``asyncio.to_thread``."""
    return None


def _install_lifespan_noops():
    """Patch the lifespan steps that need a real Postgres/Alembic.

    ``main.py`` does ``from database import init_db`` and
    ``from run_migrations import run_migrations`` (binding NEW local names),
    so we must also overwrite ``main.init_db`` / ``main.run_migrations`` —
    patching the source modules is not enough on its own.
    """
    import database
    import run_migrations

    database.init_db = _async_noop
    run_migrations.run_migrations = _sync_noop

    # ``main`` should be imported by now (in main() we call this BEFORE
    # the ``import main``), but we re-apply the patch in main() at the very
    # end too, to make this function safe to call either order.
    import main as _main  # noqa: WPS433

    _main.init_db = _async_noop
    _main.run_migrations = _sync_noop


def main():
    _install_lifespan_noops()

    import uvicorn

    # Importing main after the patch means its lifespan wrapper builds with
    # the pre-stubbed dependency functions, so the FastAPI app boots even
    # without Postgres / SQLite present.
    import main  # noqa: F401

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        log_level="info",
        reload=args.reload,
    )


if __name__ == "__main__":
    main()

import asyncio
from logging.config import fileConfig
import os
import sys
from pathlib import Path
from logging.config import fileConfig
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# --- ADD THESE LINES TO INTEGRATE YOUR PROJECT --
# 1. Add your project root directory to the Python path (absolute, handles
#    Windows paths with spaces like "Linkdin Automation")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
# 2. Import your Base metadata and database configurations
from dotenv import load_dotenv

# Load .env from the project root explicitly — os.getenv("DATABASE_URL")
# alone fails when the process is started from a different CWD (common on
# Windows double-click / PowerShell).
load_dotenv(dotenv_path=PROJECT_ROOT / ".env")
load_dotenv()  # also try CWD for backwards compatibility

try:
    from database import Base  # Or wherever your SQLAlchemy 'Base' object lives
except Exception as e:
    # Fail fast with a visible error instead of silently shutting down
    print(f"FATAL: Could not import Base from database.py: {e}", file=sys.stderr)
    raise

# 3. Reference your project metadata
target_metadata = Base.metadata
# ----------------------------------------------



# Interpret the config file for Python logging
# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
#
# IMPORTANT: Alembic's default fileConfig() wipes all existing handlers and
# sets root level to WARNING (from alembic.ini), which makes it look like logs
# disappear after migrations finish (INFO logs are hidden).  We must NOT
# disable existing loggers that the application already configured (uvicorn +
# our flushing stdout handler).  Passing disable_existing_loggers=False
# preserves the application's root handler and level, while still configuring
# the alembic/sqlalchemy loggers defined in alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

# 4. Dynamically load the database URL from your .env file
_db_url = os.getenv("DATABASE_URL")
if not _db_url:
    # Don't silently set sqlalchemy.url to "None" — that produces a confusing
    # "Will assume transactional DDL." then immediate shutdown with no traceback.
    print(
        "FATAL: DATABASE_URL is not set. Alembic cannot run migrations. "
        f"Checked {PROJECT_ROOT / '.env'} and environment. "
        "Set DATABASE_URL in .env (e.g. postgresql+asyncpg://user:pass@localhost/db)",
        file=sys.stderr,
    )
    # Set a sentinel that will fail fast with a clear driver error if somehow continued
    # but also raise here when run via lifespan (caught and logged in main.py)
    # We don't raise at import time when alembic is just --help, so only warn here.
    # The actual failure will happen in run_migrations_online() where we raise.
else:
    config.set_main_option("sqlalchemy.url", _db_url)



def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Handles being called from:
      * plain ``alembic upgrade head`` (no event loop running) — use asyncio.run
      * FastAPI lifespan via ``await asyncio.to_thread(run_migrations)`` — the
        worker thread has no running loop, so asyncio.run is also safe
      * any future direct ``await run_async_migrations()`` — detect a running
        loop and avoid ``asyncio.run() cannot be called from a running event loop``

    On Windows the default ProactorEventLoop does not support nested
    asyncio.run() inside a running loop, which previously caused the service to
    exit immediately after ``Will assume transactional DDL.`` with no traceback.
    """
    # Fail fast if DATABASE_URL was missing at import time
    if not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "DATABASE_URL is not set — cannot run online migrations. "
            "Check your .env file at the project root."
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread — safe to use asyncio.run
        asyncio.run(run_async_migrations())
    else:
        # We're inside a running event loop (e.g. someone called this directly
        # with `await`). Create a fresh loop in a dedicated thread.
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, run_async_migrations())
            # Propagate any exception instead of silently shutting down
            future.result()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

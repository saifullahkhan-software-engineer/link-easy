import asyncio
import logging
import os
import socket

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.orm import declarative_base

from core.config import settings

logger = logging.getLogger(__name__)

Base = declarative_base()

from models.linkedin_account import LinkedInAccount  # noqa: F401
from models.whatsapp import WhatsAppSession, WhatsAppMonitoredGroup, WhatsAppForwardGroup, WhatsAppRawMessage, WhatsAppScanFilter  # noqa: F401
# RBAC, admin settings, and the Postgres rate limiter must be imported here so
# ``Base.metadata.create_all`` sees them on a brand-new database (the same
# reason the models above are imported).
from models.rbac import AppSetting, Role, UserRoleLink  # noqa: F401
from models.rate_limit import RateLimitCounter  # noqa: F401
from models.social_scheduler import (
    PlatformCredential,  # noqa: F401
    ShareTarget,  # noqa: F401
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
)
from models.gmail import GmailConnection  # noqa: F401

# Connection-pool sizing.
#
# All four database engines live in ONE container (the API, the Celery worker
# and Beat run together via start.sh) and talk to the SAME Postgres. On
# Railway's bundled Postgres, connections pass through PgBouncer, which on the
# low-tier plans caps clients at 15 in total ("EMAXCONNSESSION: max clients
# reached in session mode - pool_size: 15"). SQLAlchemy's default QueuePool
# opens pool_size=5 plus max_overflow=10 — up to 15 connections PER ENGINE,
# and there are four engines (this async one + one sync engine per worker
# task module), so a burst (e.g. the long-lived live-view SSE streams during a
# WhatsApp connect) blew straight past the cap and every authenticated
# request 500'd with EMAXCONNSESSION until the connection finished.
#
# The pools are therefore bounded so their worst-case total stays well under
# the PgBouncer cap:
#   API async pool:  pool_size + max_overflow = 5
#   worker sync pools (x3): 1 + 1 each = 2 each  →  6
#   worst-case total: 11, leaving headroom for the PgBouncer/admin clients.
# Values are env-tunable so a larger Postgres plan can raise them without a
# code change.
DB_POOL_SIZE = max(1, int(os.getenv("DB_POOL_SIZE", "3")))
DB_MAX_OVERFLOW = max(0, int(os.getenv("DB_MAX_OVERFLOW", "2")))
# Wait this long for a pooled connection instead of erroring the instant the
# pool is saturated; far better to briefly queue than to 500 a request that a
# second later would have succeeded.
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "30"))
# Recycle connections periodically so PgBouncer/server-side idle timeouts never
# hand back a dead connection (pool_pre_ping also guards this).
DB_POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "1800"))

# SQLite (used by the test suite and local file-backed dev runs) uses a
# StaticPool/NullPool that does not accept pool_size/max_overflow, so only
# pass the QueuePool tuning knobs for real Postgres backends.
_is_sqlite = settings.DATABASE_URL.startswith("sqlite")
if _is_sqlite:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
    )
else:
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DEBUG,
        pool_pre_ping=True,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT,
        pool_recycle=DB_POOL_RECYCLE,
        connect_args={"timeout": 10, "command_timeout": 10},
    )

# Create a configured "Session" class.
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


# Startup retry knobs (env-tunable). The defaults give a cold start ~30s of
# runway: on Railway the Postgres plugin is frequently still accepting
# connections when the app container boots, and the previous 3x2s budget lost
# that race often enough to fail deploys. A genuinely unreachable
# DATABASE_URL still fails fast — see diagnose_connection_error(), which
# returns a one-line cause instead of the ~70-frame asyncpg traceback.
DB_CONNECT_ATTEMPTS = max(1, int(os.getenv("DB_CONNECT_ATTEMPTS", "10")))
DB_CONNECT_RETRY_DELAY = max(0.0, float(os.getenv("DB_CONNECT_RETRY_DELAY", "3")))


def database_target() -> str:
    """Credential-masked description of the configured DB (safe to log)."""
    try:
        url = make_url(settings.DATABASE_URL)
        return f"host={url.host} port={url.port or 5432} db={url.database}"
    except Exception:
        return "<DATABASE_URL could not be parsed>"


def _exception_chain(exc: BaseException) -> list[BaseException]:
    """Return exc plus its __cause__/__context__ ancestors (cycle-safe)."""
    seen: set[int] = set()
    chain: list[BaseException] = []
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    return chain


def diagnose_connection_error(exc: BaseException) -> str:
    """Turn a low-level connect failure into a short, actionable message.

    The raw asyncpg/SQLAlchemy traceback is ~70 frames of DNS/socket plumbing
    that say nothing beyond the final line; on a crash-looping deploy it is
    printed twice (app logger + uvicorn lifespan handler) every few seconds,
    which blows past Railway's 500 logs/sec cap and gets the *useful* lines
    dropped. This returns the useful line.
    """
    for e in reversed(_exception_chain(exc)):  # innermost cause first
        msg = str(e)
        if isinstance(e, socket.gaierror) or "Name or service not known" in msg:
            return (
                f"DNS lookup failed for the database host ({type(e).__name__}: {msg}). "
                f"DATABASE_URL ({database_target()}) does not resolve from inside this "
                "container. On Railway, set DATABASE_URL to a live Postgres reference "
                "such as ${{Postgres.DATABASE_PRIVATE_URL}} (same project, private "
                "network) or ${{Postgres.DATABASE_URL}} (public proxy) — a literal "
                "host like 'postgres' or 'localhost' only works inside docker-compose."
            )
        if isinstance(e, ConnectionRefusedError) or "Connection refused" in msg:
            return (
                f"Connection refused ({msg}). The DATABASE_URL host resolved but "
                f"nothing is listening ({database_target()}) — check that the Postgres "
                "service is actually running and the port matches."
            )
        if "password authentication failed" in msg.lower() or "authentication failed" in msg.lower():
            return (
                f"Postgres rejected the credentials ({msg}). The DATABASE_URL "
                "username/password is wrong or the user has no access to this database."
            )
        if isinstance(e, (TimeoutError, socket.timeout)) or "timed out" in msg.lower():
            return (
                f"Timed out connecting to the database ({database_target()}). The host "
                "resolves but is unreachable — a firewall/VPC issue or an overloaded "
                "database."
            )
    return f"{type(exc).__name__}: {exc}"


def _is_connectivity_error(exc: BaseException) -> bool:
    """True when retrying could plausibly help (DNS/connect/auth/timeouts)."""
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    return any(
        isinstance(e, (socket.gaierror, ConnectionError, TimeoutError))
        for e in _exception_chain(exc)
    )


async def init_db() -> None:
    """Initializes the database and creates tables if they don't exist.

    Transient connectivity failures are retried with short backoff (Postgres
    may lag behind the app container at boot). On final failure a *short*
    RuntimeError with an actionable diagnosis is raised — see
    ``diagnose_connection_error`` for why the original traceback is dropped.
    Non-connectivity errors (e.g. SQL problems in create_all) fail immediately.
    """
    last_exc: BaseException | None = None
    attempt = 0
    for attempt in range(1, DB_CONNECT_ATTEMPTS + 1):
        try:
            async with engine.begin() as conn:
                # This will create all tables defined in models that inherit from Base
                await conn.run_sync(Base.metadata.create_all)
            return
        except Exception as exc:
            last_exc = exc
            if not _is_connectivity_error(exc):
                break  # schema/SQL problem — retrying cannot help
            if attempt < DB_CONNECT_ATTEMPTS:
                logger.warning(
                    "Postgres not reachable yet (attempt %d/%d): %s — retrying in %.0fs",
                    attempt,
                    DB_CONNECT_ATTEMPTS,
                    diagnose_connection_error(exc),
                    DB_CONNECT_RETRY_DELAY,
                )
                await asyncio.sleep(DB_CONNECT_RETRY_DELAY)
    raise RuntimeError(
        f"init_db() failed (attempt {attempt}/{DB_CONNECT_ATTEMPTS}): "
        f"{diagnose_connection_error(last_exc)}"
    ) from None

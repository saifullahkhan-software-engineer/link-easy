"""Bounded sync SQLAlchemy engine shared by the Celery task modules.

WHY THIS EXISTS
---------------
The API, the Celery worker and Celery Beat all run inside the SAME container
(see start.sh) and hit the SAME Postgres. Four SQLAlchemy engines live there:
the async API engine (database.py) plus one sync engine per task module —
campaign_tasks, feed_scroll_tasks and whatsapp_tasks.

SQLAlchemy's default QueuePool opens pool_size=5 + max_overflow=10 = up to 15
connections PER ENGINE. On Railway's bundled Postgres, connections traverse
PgBouncer, which on low-tier plans caps the total client count at ~15
("EMAXCONNSESSION: max clients reached in session mode - pool_size: 15").
Four unbounded engines — made worse by the long-lived live-view SSE streams
pinning connections for minutes — blew straight past that cap, so every
authenticated request 500'd during a WhatsApp connect.

Each worker pool is therefore kept tiny (1 pooled + 1 overflow by default):
the Celery worker runs one task at a time (concurrency=1,
worker_prefetch_multiplier=1), so that is ample. All values are env-tunable
for larger Postgres plans:
    DB_WORKER_POOL_SIZE (default 1)
    DB_WORKER_MAX_OVERFLOW (default 1)
    DB_POOL_TIMEOUT (default 30 seconds)
    DB_POOL_RECYCLE (default 1800 seconds)
"""
import os

from sqlalchemy import create_engine

# Worst-case total this yields with the API defaults (pool 3 + overflow 2):
#   API:        5
#   3 workers:  3 * (1 + 1) = 6
#   total:     11  → under PgBouncer's 15-client cap, with headroom.


def make_worker_engine(sync_url: str):
    """Create a bounded sync engine.

    SQLite (used by the test suite) uses a StaticPool that rejects the
    QueuePool tuning kwargs, so those are only applied for real Postgres URLs.
    """
    if sync_url.startswith("sqlite"):
        return create_engine(sync_url, pool_pre_ping=True)

    pool_size = max(1, int(os.getenv("DB_WORKER_POOL_SIZE", "1")))
    max_overflow = max(0, int(os.getenv("DB_WORKER_MAX_OVERFLOW", "1")))
    return create_engine(
        sync_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=float(os.getenv("DB_POOL_TIMEOUT", "30")),
        pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
    )

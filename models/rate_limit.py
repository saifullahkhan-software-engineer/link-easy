"""
Postgres-backed rate limiting.

FILE: models/rate_limit.py

Redis on this deployment is saturated by Celery job traffic, so API rate
limiting lives in Postgres instead. One row per (identity, bucket, window):

    identity  — the caller: "user:me@example.com" or "ip:203.0.113.7"
    bucket    — the protected surface: "auth:login", "profile:scan", ...
    window_started_at — start of the current fixed window

Counting uses a fixed-window counter, incremented atomically with a single
``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` so two concurrent requests
cannot both read a stale count (the classic read-then-write race). Expired
rows are pruned opportunistically, so no cron job is required.
"""
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from database import Base


class RateLimitCounter(Base):
    """A single fixed-window request counter."""

    __tablename__ = "rate_limit_counters"
    __table_args__ = (
        # The upsert target: one counter per identity+bucket+window.
        UniqueConstraint(
            "identity", "bucket", "window_started_at", name="uq_rate_limit_window"
        ),
        # Supports the opportunistic prune of stale windows.
        Index("ix_rate_limit_window_started_at", "window_started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    identity = Column(String(320), nullable=False, index=True)
    bucket = Column(String(80), nullable=False, index=True)
    window_started_at = Column(DateTime(timezone=True), nullable=False)
    request_count = Column(Integer, nullable=False, default=0)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

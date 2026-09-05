"""
Postgres fixed-window rate limiter.

FILE: services/rate_limiter.py

Redis is fully occupied with Celery job traffic on this deployment, so API
rate limiting is enforced in Postgres.

Why a single upsert and not read-then-write
-------------------------------------------
The obvious implementation (SELECT the counter, add one, UPDATE) has a
time-of-check/time-of-use race: two requests arriving together both read
``count = limit - 1`` and both conclude they are allowed, so the effective
limit is exceeded. Instead every check is ONE statement::

    INSERT INTO rate_limit_counters (...) VALUES (...)
    ON CONFLICT (identity, bucket, window_started_at)
    DO UPDATE SET request_count = rate_limit_counters.request_count + 1
    RETURNING request_count

Postgres serialises conflicting upserts on the unique index, so the value
returned is already the caller's own position in the window. SQLite (used by
the test suite) gets an equivalent ``ON CONFLICT DO UPDATE``.

Fail-open
---------
If the limiter itself errors (database blip, missing table before the
migration runs) the request is ALLOWED. A monitoring hiccup must never take
down login for everyone; the failure is logged instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.logging_config import get_logger
from models.rate_limit import RateLimitCounter

logger = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitRule:
    """A limit of ``max_requests`` per ``window_seconds`` for one bucket."""

    bucket: str
    max_requests: int
    window_seconds: int
    description: str = ""

    @property
    def settings_prefix(self) -> str:
        """Key prefix used to override this rule from ``app_settings``."""
        return f"rate_limit.{self.bucket}"


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    bucket: str


# ── Default rules ────────────────────────────────────────────────────────────
# Auth buckets blunt credential-stuffing / brute force. The browser buckets
# protect genuinely expensive work: each one drives a real Chromium session.
DEFAULT_RULES: dict[str, RateLimitRule] = {
    rule.bucket: rule
    for rule in (
        RateLimitRule("auth:login", 10, 300, "Login attempts per 5 minutes"),
        RateLimitRule("auth:register", 5, 3600, "Signups per hour"),
        RateLimitRule("auth:forgot-password", 5, 3600, "Password reset emails per hour"),
        RateLimitRule("auth:reset-password", 10, 3600, "Password reset submissions per hour"),
        RateLimitRule("auth:verify-email", 20, 3600, "Email verification attempts per hour"),
        RateLimitRule("auth:resend-verification", 5, 3600, "Verification resends per hour"),
        # Account-deletion flow (public endpoints, no auth): the request
        # endpoint is scoped per submitted email so an attacker cannot spam a
        # victim's inbox, and the confirm endpoint is scoped per caller so a
        # guessed token cannot be brute-forced.
        RateLimitRule("user-data:request", 5, 3600, "Account-deletion emails per hour"),
        RateLimitRule("user-data:confirm", 10, 3600, "Account-deletion confirmations per hour"),
        RateLimitRule("profile:scan", 20, 3600, "LinkedIn profile scans per hour"),
        # Each call spends tokens on a third-party LLM, so it is metered per
        # user like the other expensive operations. 60/hour is far above what
        # the upload editor needs (one extraction per video) and still stops
        # a looped client from running up a bill.
        RateLimitRule("social:parse-copy", 60, 3600, "AI copy extractions per hour"),
        RateLimitRule("live:start", 30, 3600, "Live-chat browser starts per hour"),
        # Gmail writes go to the user's real mailbox, so sending is metered
        # like every other side-effectful action. 60/hour is far below
        # Google's own ~500/day consumer limit and still plenty for a human
        # replying from the inbox.
        RateLimitRule("gmail:send", 60, 3600, "Gmail messages sent per hour"),
        # The inbox page's "checking mail" tick plus explicit refreshes. Each
        # call fans out into a handful of Gmail API requests, so it is capped
        # per user per hour.
        RateLimitRule("gmail:check", 300, 3600, "Gmail check calls per hour"),
    )
}

# Windows older than this multiple of their own length are safe to delete.
_PRUNE_AFTER_WINDOWS = 3
# Prune at most this often (seconds) to keep the hot path cheap.
_PRUNE_INTERVAL_SECONDS = 300
_last_prune_at: Optional[datetime] = None


def _window_start(now: datetime, window_seconds: int) -> datetime:
    """Snap ``now`` down to the start of its fixed window."""
    epoch_seconds = int(now.timestamp())
    return datetime.fromtimestamp(
        epoch_seconds - (epoch_seconds % window_seconds), tz=timezone.utc
    )


async def _maybe_prune(db: AsyncSession, now: datetime) -> None:
    """Delete long-expired counters occasionally (no cron needed)."""
    global _last_prune_at
    if _last_prune_at is not None and (now - _last_prune_at).total_seconds() < _PRUNE_INTERVAL_SECONDS:
        return
    _last_prune_at = now

    longest_window = max((r.window_seconds for r in DEFAULT_RULES.values()), default=3600)
    cutoff = now - timedelta(seconds=longest_window * _PRUNE_AFTER_WINDOWS)
    try:
        await db.execute(
            delete(RateLimitCounter).where(RateLimitCounter.window_started_at < cutoff)
        )
        await db.commit()
    except Exception as exc:  # pragma: no cover - best effort
        logger.debug("Rate-limit prune skipped: %s", exc)
        try:
            await db.rollback()
        except Exception:
            pass


async def check_rate_limit(
    db: AsyncSession,
    identity: str,
    rule: RateLimitRule,
    *,
    now: Optional[datetime] = None,
) -> RateLimitResult:
    """Consume one unit from ``identity``'s window for ``rule``.

    Returns the decision. Always fails OPEN: a limiter error allows the
    request rather than locking users out of the product.
    """
    now = now or datetime.now(timezone.utc)
    window_started_at = _window_start(now, rule.window_seconds)

    # One statement, so concurrent callers cannot both see a stale count.
    # ``excluded`` is the row that failed to insert (standard upsert syntax,
    # supported by both PostgreSQL and SQLite).
    statement = text(
        """
        INSERT INTO rate_limit_counters
            (identity, bucket, window_started_at, request_count, created_at, updated_at)
        VALUES
            (:identity, :bucket, :window_started_at, 1, :now, :now)
        ON CONFLICT (identity, bucket, window_started_at)
        DO UPDATE SET
            request_count = rate_limit_counters.request_count + 1,
            updated_at = :now
        RETURNING request_count
        """
    )

    try:
        result = await db.execute(
            statement,
            {
                "identity": identity,
                "bucket": rule.bucket,
                "window_started_at": window_started_at,
                "now": now,
            },
        )
        used = int(result.scalar_one())
        await db.commit()
    except Exception as exc:
        # Fail open — never block real users because the limiter broke.
        # The rollback is itself best-effort: if the session is unusable (a
        # dropped connection, or no session at all) letting that second error
        # escape would defeat the whole point of failing open.
        try:
            await db.rollback()
        except Exception:  # pragma: no cover - defensive
            pass
        logger.warning(
            "Rate limit check failed for %s/%s — allowing request: %s",
            identity,
            rule.bucket,
            exc,
        )
        return RateLimitResult(True, rule.max_requests, rule.max_requests, 0, rule.bucket)

    await _maybe_prune(db, now)

    allowed = used <= rule.max_requests
    window_ends_at = window_started_at + timedelta(seconds=rule.window_seconds)
    retry_after = max(1, int((window_ends_at - now).total_seconds())) if not allowed else 0

    if not allowed:
        logger.info(
            "⛔ Rate limit hit: identity=%s bucket=%s used=%s limit=%s",
            identity,
            rule.bucket,
            used,
            rule.max_requests,
        )

    return RateLimitResult(
        allowed=allowed,
        limit=rule.max_requests,
        remaining=max(0, rule.max_requests - used),
        retry_after_seconds=retry_after,
        bucket=rule.bucket,
    )


async def resolve_rule(db: AsyncSession, bucket: str) -> RateLimitRule:
    """Return ``bucket``'s rule, applying any admin override.

    Admins can retune limits from the dashboard without a redeploy; the
    override lives in ``app_settings`` under ``rate_limit.<bucket>.*``.
    """
    base = DEFAULT_RULES[bucket]
    try:
        from services.app_settings import get_settings_map

        overrides = await get_settings_map(db, category="rate_limit")
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not read rate-limit overrides: %s", exc)
        return base

    max_requests = overrides.get(f"{base.settings_prefix}.max_requests")
    window_seconds = overrides.get(f"{base.settings_prefix}.window_seconds")
    if max_requests is None and window_seconds is None:
        return base

    try:
        return RateLimitRule(
            bucket=base.bucket,
            max_requests=int(max_requests if max_requests is not None else base.max_requests),
            window_seconds=int(
                window_seconds if window_seconds is not None else base.window_seconds
            ),
            description=base.description,
        )
    except (TypeError, ValueError):
        return base

"""
Redis-backed per-account profile lock.
FILE: worker/profile_lock.py

A Chromium user-data-dir can only be held by one process at a time (Chromium's
SingletonLock). With persistent profiles, a given account's profile directory
can be reached from three places — the Celery campaign session task, the
session-verification endpoint, and the interactive login flow — so exactly one
of them may open the profile at any moment.

This lock is that guard. It is DISTINCT from worker/playwright_semaphore.py:

  * playwright_semaphore  → caps the TOTAL number of concurrent browser
    processes across ALL accounts (global resource pressure).
  * profile_lock          → prevents two callers from racing on the SAME
    account's profile directory (correctness / anti-corruption).

Both apply: acquire a semaphore slot AND the account's profile lock before
launching a persistent context.

Usage:
    lock = acquire_profile_lock(account.id)   # raises ProfileInUseError fast
    try:
        pw, _, context, page = await launch_persistent_browser(account)
        ...
    finally:
        await context.close()
        await pw.stop()
        release_profile_lock(lock)

If the lock is already held, the caller fails immediately with a clear
"account is currently in use" error instead of attempting to launch and
failing confusingly on Chromium's SingletonLock.
"""
import redis

from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# Sync Redis client for use in both Celery tasks and FastAPI handlers
# (acquire/release are single fast Redis round-trips).
_redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Auto-expiry safety net — slightly longer than SESSION_DURATION_MAX (20 min)
# in worker/tasks/campaign_tasks.py so a healthy session never loses its lock
# mid-run. If a holder crashes without releasing, the lock frees itself.
PROFILE_LOCK_TIMEOUT = 30 * 60  # seconds

# How long to wait for a held lock before failing fast. Default is short so
# callers get a clear error rather than hanging; pass 0 for "never block".
PROFILE_LOCK_BLOCKING_TIMEOUT = 5  # seconds


class ProfileInUseError(Exception):
    """Raised when an account's profile directory is already open elsewhere."""


def _lock_key(account_id: str) -> str:
    # Keyed by the server-generated account UUID — never by user input.
    return f"profile_lock:{account_id}"


def acquire_profile_lock(account_id: str, blocking_timeout: int | float = PROFILE_LOCK_BLOCKING_TIMEOUT):
    """
    Acquire the per-account profile lock.

    Args:
        account_id: LinkedInAccount.id (server-generated UUID).
        blocking_timeout: seconds to wait for a held lock. 0 = fail
            immediately if held. Raises ProfileInUseError on failure.

    Returns:
        The redis.lock.Lock object — pass it to release_profile_lock() in a
        finally block (or hand it to the session manager for keep-alive flows).
    """
    lock = _redis.lock(
        _lock_key(account_id),
        timeout=PROFILE_LOCK_TIMEOUT,
        blocking_timeout=blocking_timeout if blocking_timeout else None,
    )

    acquired = lock.acquire(blocking=bool(blocking_timeout))
    if not acquired:
        raise ProfileInUseError(
            f"LinkedIn account {account_id} is currently in use by another "
            f"session (its browser profile is locked). Please try again in a "
            f"few minutes."
        )
    logger.debug("🔒 Acquired profile lock for account %s", account_id)
    return lock


def release_profile_lock(lock) -> None:
    """
    Release a profile lock. Safe to call with None or with a lock that has
    already expired/been released (e.g. after a crash) — never raises.
    """
    if lock is None:
        return
    try:
        lock.release()
        logger.debug("🔓 Released profile lock")
    except redis.exceptions.LockNotOwnedError:
        # Lock TTL expired (crash/restart guard) — nothing to release.
        logger.warning("⚠️ Profile lock had already expired before release")
    except redis.exceptions.LockError:
        # Already released / not owned by this token — benign (e.g. released
        # elsewhere after expiry). Never propagate.
        logger.debug("Profile lock was already released before release()")
    except Exception:
        logger.warning("⚠️ Failed to release profile lock", exc_info=True)

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


def profile_in_use_message(account_id: str) -> str:
    """Human-readable lock conflict. WhatsApp shares this helper too."""
    if str(account_id) == "whatsapp":
        return (
            "WhatsApp is currently in use by another session "
            "(its browser profile is locked). Please try again in a few minutes."
        )
    return (
        f"LinkedIn account {account_id} is currently in use by another "
        f"session (its browser profile is locked). Please try again in a "
        f"few minutes."
    )


def is_profile_lock_held(account_id: str) -> bool:
    """True when the Redis lock key exists (held or not-yet-expired crash leftover)."""
    try:
        return bool(_redis.exists(_lock_key(account_id)))
    except Exception:
        logger.warning("⚠️ Could not inspect profile lock for %s", account_id, exc_info=True)
        return False


def force_release_profile_lock(account_id: str) -> bool:
    """Delete the Redis lock key without owning the token.

    Use only when no living process holds the Chromium profile — a crashed
    API worker or Celery task otherwise leaves ``profile_lock:{id}`` for the
    full 30-minute TTL and every Connect/Start looks broken even though the
    UI shows no active account.
    """
    try:
        deleted = bool(_redis.delete(_lock_key(account_id)))
    except Exception:
        logger.warning(
            "⚠️ Could not force-release profile lock for %s", account_id, exc_info=True
        )
        return False
    if deleted:
        logger.warning("🔓 Force-released stale profile lock for %s", account_id)
    return deleted


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
        # The token must be shared across threads. We routinely acquire this
        # lock inside an ``asyncio.to_thread`` worker (WhatsApp browser view /
        # live chat) or a Celery task thread and release it from the FastAPI
        # event-loop thread. With the default thread-local token, release()
        # from the other thread hits an AttributeError ("Profile lock object
        # state corrupted"), the key is NEVER deleted, and it sits in Redis
        # for the full 30-minute TTL blocking every later Connect until
        # someone force-releases it. Each acquire_profile_lock() call builds
        # a fresh Lock object, so the cross-thread-release footgun documented
        # in redis-py (another thread re-acquiring an expired lock) cannot
        # happen here.
        thread_local=False,
    )

    acquired = lock.acquire(blocking=bool(blocking_timeout))
    if not acquired:
        raise ProfileInUseError(profile_in_use_message(account_id))
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
    except AttributeError:
        # Lock object's internal state is corrupted (e.g. missing token).
        # Mostly historical: this happened whenever a lock acquired in an
        # asyncio.to_thread worker was released from another thread (the
        # thread-local token was invisible there). acquire_profile_lock()
        # now creates locks with thread_local=False, so this is only a
        # safety net for foreign lock objects. Treat as already released.
        logger.warning("⚠️ Profile lock object state corrupted, treating as released")
    except Exception:
        logger.warning("⚠️ Failed to release profile lock", exc_info=True)

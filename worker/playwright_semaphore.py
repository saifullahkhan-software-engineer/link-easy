"""
Redis-based semaphore for limiting concurrent Playwright sessions.
FILE: worker/playwright_semaphore.py

Ensures no more than MAX_CONCURRENT_PLAYWRIGHT sessions run globally,
even when multiple users start campaigns simultaneously.
"""
import redis
import time
from contextlib import contextmanager
from core.config import settings

# Sync Redis client for use inside Celery tasks
_redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Maximum concurrent Playwright sessions allowed globally
MAX_CONCURRENT_PLAYWRIGHT = 2

# Redis key for the semaphore
SEMAPHORE_KEY = "playwright:semaphore"
# Lock key to prevent race conditions
LOCK_KEY = "playwright:lock"


@contextmanager
def acquire_playwright_session(timeout: int = 300):
    """
    Context manager to acquire a Playwright session slot.
    
    Args:
        timeout: Maximum time to wait for a slot (seconds). Default: 5 minutes.
    
    Yields:
        True if slot acquired, False if timeout reached.
    
    Usage:
        with acquire_playwright_session() as acquired:
            if acquired:
                # Run Playwright code
                pass
            else:
                # Handle timeout
                pass
    """
    start_time = time.time()
    
    while time.time() - start_time < timeout:
        # Try to acquire a slot using Redis INCR
        current = _redis.incr(SEMAPHORE_KEY)
        
        if current <= MAX_CONCURRENT_PLAYWRIGHT:
            # Slot acquired - set expiry to auto-release if process crashes
            _redis.expire(SEMAPHORE_KEY, 3600)  # 1 hour expiry
            
            try:
                yield True
                return
            finally:
                # Release the slot
                _redis.decr(SEMAPHORE_KEY)
        else:
            # No slot available - decrement and wait
            _redis.decr(SEMAPHORE_KEY)
            time.sleep(5)  # Wait 5 seconds before retrying
    
    # Timeout reached
    yield False


def get_active_session_count() -> int:
    """Returns the current number of active Playwright sessions."""
    count = _redis.get(SEMAPHORE_KEY)
    return int(count) if count else 0


def reset_semaphore():
    """
    Resets the semaphore to 0. Use only in emergencies or debugging.
    This should not be called during normal operation.
    """
    _redis.delete(SEMAPHORE_KEY)

"""
Redis-based daily action rate limiter.
FILE: worker/rate_limit.py
 
Uses Redis INCR + EXPIRE to count actions per account per day.
Keys expire at midnight automatically — no cleanup needed.
"""
import redis
from datetime import datetime, timezone
from core.config import settings
 
# Sync Redis client for use inside Celery tasks
_redis = redis.from_url(settings.REDIS_URL, decode_responses=True)
 
# Hard caps — these cannot be overridden by campaign settings
HARD_CAPS = {
    "visit_profile":    80,
    "like_post":        30,
    "send_connection":  15,
    "send_message":     20,
    "endorsement":       5,
}
 
 
def _key(account_email: str, action: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"rate:{account_email}:{action}:{today}"
 
 
def check_and_increment(account_email: str, action: str, campaign_limit: int | None = None) -> bool:
    """
    Returns True if the action is allowed and increments the counter.
    Returns False if the daily limit has been reached.
 
    campaign_limit: optional per-campaign override (must be <= HARD_CAP)
    """
    hard_cap = HARD_CAPS.get(action, 50)
    limit = min(campaign_limit, hard_cap) if campaign_limit else hard_cap
 
    key = _key(account_email, action)
    current = _redis.get(key)
 
    if current and int(current) >= limit:
        return False  # Daily limit reached
 
    pipe = _redis.pipeline()
    pipe.incr(key)
    pipe.expireat(key, _seconds_until_midnight())
    pipe.execute()
    return True
 
 
def get_count(account_email: str, action: str) -> int:
    """Returns current count for an action today."""
    key = _key(account_email, action)
    val = _redis.get(key)
    return int(val) if val else 0
 
 
def _seconds_until_midnight() -> int:
    """Returns Unix timestamp of next UTC midnight."""
    import calendar
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = midnight.replace(day=midnight.day + 1)
    return int(calendar.timegm(tomorrow.timetuple()))

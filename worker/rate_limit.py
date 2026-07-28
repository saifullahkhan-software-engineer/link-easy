"""
Redis-based daily/weekly action rate limiter with account warm-up pacing.
FILE: worker/rate_limit.py

Uses Redis INCR + EXPIRE to count actions per account per day (and per week
for warming accounts). Daily keys expire at midnight automatically — no
cleanup needed.

Two layers of caps:
  1. HARD_CAPS — absolute daily ceilings that campaign settings can never
     exceed (established accounts).
  2. WARMUP_DAILY_CAPS / WARMUP_WEEKLY_CAPS — materially lower ceilings for
     new accounts in their first ~2-3 weeks, ramping up gradually. This is
     DISTINCT from the per-session MAX_ACTIONS_PER_SESSION cap in
     worker/tasks/campaign_tasks.py (that one limits a single browser
     session; these limit the whole day/week across all sessions).

The warm-up stage is derived from account age by default
(warmup_stage_for_account) and can be pinned manually via
LinkedInAccount.warmup_stage.
"""
import redis
from datetime import datetime, timezone
from core.config import settings

# Sync Redis client for use inside Celery tasks
_redis = redis.from_url(settings.REDIS_URL, decode_responses=True)

# Hard caps — these cannot be overridden by campaign settings (established accounts)
HARD_CAPS = {
    "visit_profile":    80,
    "like_post":        30,
    "send_connection":  15,
    "send_message":     20,
    "endorsement":       5,
}

# ── Warm-up pacing ────────────────────────────────────────────────────────────
# New accounts get a materially lower daily ceiling for the first ~2-3 weeks,
# ramping up gradually. Variance only between accounts of different ages —
# never random per-session.
WARMUP_STAGES = ("new", "ramping", "established")

# Warm-up stage thresholds in account-age days
NEW_STAGE_MAX_AGE_DAYS = 14       # days 0-13  → "new"
RAMPING_STAGE_MAX_AGE_DAYS = 28   # days 14-27 → "ramping"; 28+ → "established"

WARMUP_DAILY_CAPS = {
    "new": {
        "visit_profile":   12,
        "like_post":        6,
        "send_connection":  3,
        "send_message":     4,
        "endorsement":      2,
    },
    "ramping": {
        "visit_profile":   30,
        "like_post":       12,
        "send_connection":  7,
        "send_message":    10,
        "endorsement":      3,
    },
    # Established accounts use HARD_CAPS directly.
    "established": HARD_CAPS,
}

# Weekly ceilings (rolling ISO week) — only enforced while warming; an extra
# brake on top of the daily caps for new accounts.
WARMUP_WEEKLY_CAPS = {
    "new": {
        "visit_profile":   50,
        "like_post":       25,
        "send_connection": 12,
        "send_message":    15,
        "endorsement":      8,
    },
    "ramping": {
        "visit_profile":  120,
        "like_post":       60,
        "send_connection": 30,
        "send_message":    40,
        "endorsement":     15,
    },
    "established": {},  # no weekly cap beyond HARD_CAPS once established
}


def warmup_stage_for_account(account) -> str:
    """
    Resolve the warm-up stage for an account.

    Explicit LinkedInAccount.warmup_stage wins (manual override); otherwise
    the stage is derived from account age:
        < 14 days  → "new"
        14-27 days → "ramping"
        >= 28 days → "established"
    """
    override = getattr(account, "warmup_stage", None)
    if override:
        return override.value if hasattr(override, "value") else str(override)

    created = getattr(account, "created_at", None)
    if created is None:
        return "established"
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (datetime.now(timezone.utc) - created).days
    if age_days < NEW_STAGE_MAX_AGE_DAYS:
        return "new"
    if age_days < RAMPING_STAGE_MAX_AGE_DAYS:
        return "ramping"
    return "established"


def _key(account_email: str, action: str) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"rate:{account_email}:{action}:{today}"


def _week_key(account_email: str, action: str) -> str:
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.isocalendar()
    return f"rate:{account_email}:{action}:week:{iso_year}W{iso_week:02d}"


def check_and_increment(account_email: str, action: str, campaign_limit: int | None = None,
                        warmup_stage: str | None = None) -> bool:
    """
    Returns True if the action is allowed and increments the counter(s).
    Returns False if the daily (or weekly, for warming accounts) limit has
    been reached.

    campaign_limit: optional per-campaign override (must be <= effective cap)
    warmup_stage:   the account's warm-up stage (see warmup_stage_for_account).
                    When provided, the stage's lower daily/weekly caps apply
                    on top of HARD_CAPS.
    """
    hard_cap = HARD_CAPS.get(action, 50)
    weekly_cap = None
    if warmup_stage in WARMUP_DAILY_CAPS:
        stage_daily = WARMUP_DAILY_CAPS[warmup_stage]
        hard_cap = min(hard_cap, stage_daily.get(action, hard_cap))
        weekly_cap = WARMUP_WEEKLY_CAPS.get(warmup_stage, {}).get(action)

    limit = min(campaign_limit, hard_cap) if campaign_limit else hard_cap

    day_key = _key(account_email, action)
    current = _redis.get(day_key)

    if current and int(current) >= limit:
        return False  # Daily limit reached

    if weekly_cap is not None:
        week_key = _week_key(account_email, action)
        week_current = _redis.get(week_key)
        if week_current and int(week_current) >= weekly_cap:
            return False  # Weekly warm-up limit reached

    pipe = _redis.pipeline()
    pipe.incr(day_key)
    pipe.expireat(day_key, _seconds_until_midnight())
    if weekly_cap is not None:
        pipe.incr(week_key)
        pipe.expire(week_key, 7 * 86400)  # rolling-week key, expires in 7 days
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

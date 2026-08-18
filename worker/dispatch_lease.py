"""Small Redis leases used by the durable Celery dispatchers.

A database timestamp is the source of truth for scheduled automation.  Beat
must still put a message on the broker, but it must not put the same message
there on every tick while the browser task is running.  These short-lived,
token-owned leases cover that gap without turning the Redis key into campaign
state.
"""
from __future__ import annotations

import uuid
from typing import Any


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def claim_dispatch_lease(redis_client: Any, key: str, timeout: int = 7200) -> str | None:
    """Claim ``key`` and return an owner token, or ``None`` if already held."""
    token = uuid.uuid4().hex
    try:
        if redis_client.set(key, token, nx=True, ex=max(30, int(timeout))):
            return token
    except Exception:
        # The caller decides whether a Redis failure should fail the dispatcher.
        # Never pretend a lease was acquired when Redis did not confirm it.
        raise
    return None


def release_dispatch_lease(redis_client: Any, key: str, token: str | None) -> None:
    """Release a lease only when it is still owned by ``token``."""
    if not token:
        return
    try:
        redis_client.eval(_RELEASE_SCRIPT, 1, key, token)
    except Exception:
        # TTL is the safety net.  A cleanup failure must not mask the task's
        # actual result or make a worker task fail after its browser is closed.
        try:
            if redis_client.get(key) == token:
                redis_client.delete(key)
        except Exception:
            pass

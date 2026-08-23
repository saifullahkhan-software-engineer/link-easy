"""Regression tests: the WhatsApp profile lock must be releasable cross-thread.

``_claim_whatsapp_profile_lock`` (browser view / live chat) acquires the lock
inside an ``asyncio.to_thread`` worker; ``release_profile_lock`` runs on the
FastAPI event-loop thread. With redis-py's default thread-local token the
release thread cannot see the token, ``release()`` raises AttributeError, the
"Profile lock object state corrupted" warning fires, and the key is never
deleted — it then blocks every later Connect until the 30-minute TTL expires
or a force-release steals it. These tests pin ``thread_local=False``.
"""
import os
import threading
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import redis  # noqa: E402

from worker.profile_lock import acquire_profile_lock, release_profile_lock  # noqa: E402


def _make_real_lock(thread_local: bool):
    """A genuine redis.lock.Lock over a mocked Redis client.

    redis-py caches the registered Lua scripts on the Lock CLASS
    (``Lock.lua_release`` etc.), so every Lock in the process shares the
    first instance's script mocks. Reset them so each test gets fresh,
    unpolluted mocks to assert on.
    """
    for attr in ("lua_release", "lua_extend", "lua_reacquire"):
        setattr(redis.lock.Lock, attr, None)
    client = Mock()
    client.register_script.side_effect = lambda script: Mock(return_value=1)
    lock = redis.lock.Lock(client, "profile_lock:whatsapp", thread_local=thread_local)
    return lock, client


class CrossThreadReleaseTests(unittest.TestCase):
    def test_release_from_other_thread_actually_releases(self):
        lock, client = _make_real_lock(thread_local=False)
        lock.local.token = b"token-abc"  # simulated acquire in thread A

        errors = []

        def releaser():
            try:
                release_profile_lock(lock)  # released from thread B
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=releaser)
        thread.start()
        thread.join()

        self.assertEqual(errors, [])
        # The Lua release script ran → the Redis key was actually deleted.
        lock.lua_release.assert_called_once()

    def test_thread_local_lock_cannot_release_cross_thread(self):
        # Documents the root cause: with the default thread-local token a
        # foreign thread gets AttributeError and the key is leaked.
        lock, client = _make_real_lock(thread_local=True)
        lock.local.token = b"token-abc"

        def releaser():
            with self.assertRaises(AttributeError):
                lock.release()

        thread = threading.Thread(target=releaser)
        thread.start()
        thread.join()
        # No release script call happened → key would stay for the TTL.
        lock.lua_release.assert_not_called()

    def test_release_profile_lock_treats_foreign_thread_as_released(self):
        # Even when a (foreign) thread-local lock sneaks through,
        # release_profile_lock must never raise — it logs the corrupted
        # warning and moves on.
        lock, client = _make_real_lock(thread_local=True)
        lock.local.token = b"token-abc"

        errors = []
        thread = threading.Thread(
            target=lambda: errors.append(release_profile_lock(lock))
        )
        thread.start()
        thread.join()
        self.assertEqual(errors, [None])


class AcquireUsesProcessWideTokenTests(unittest.TestCase):
    def test_acquire_asks_redis_for_thread_local_false(self):
        lock = Mock()
        lock.acquire.return_value = True
        client = Mock()
        client.lock.return_value = lock
        with patch("worker.profile_lock._redis", client):
            acquired = acquire_profile_lock("whatsapp", blocking_timeout=0)
        self.assertIs(acquired, lock)
        kwargs = client.lock.call_args.kwargs
        self.assertIn("thread_local", kwargs)
        self.assertFalse(kwargs["thread_local"])


if __name__ == "__main__":
    unittest.main()

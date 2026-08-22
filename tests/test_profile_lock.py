"""Unit tests for the Redis profile lock helpers."""
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from worker.profile_lock import (  # noqa: E402
    ProfileInUseError,
    acquire_profile_lock,
    force_release_profile_lock,
    is_profile_lock_held,
    profile_in_use_message,
)


class ProfileLockMessageTests(unittest.TestCase):
    def test_whatsapp_error_does_not_mention_linkedin(self):
        message = profile_in_use_message("whatsapp")
        self.assertIn("WhatsApp", message)
        self.assertNotIn("LinkedIn", message)
        self.assertNotIn("account whatsapp", message.lower())

    def test_linkedin_uuid_keeps_existing_wording(self):
        message = profile_in_use_message("acc-123")
        self.assertIn("LinkedIn account acc-123", message)


class ProfileLockForceReleaseTests(unittest.TestCase):
    def test_force_release_deletes_the_redis_key(self):
        redis_client = unittest.mock.Mock()
        redis_client.delete.return_value = 1
        with patch("worker.profile_lock._redis", redis_client):
            self.assertTrue(force_release_profile_lock("whatsapp"))
        redis_client.delete.assert_called_once_with("profile_lock:whatsapp")

    def test_is_held_reads_exists(self):
        redis_client = unittest.mock.Mock()
        redis_client.exists.return_value = 1
        with patch("worker.profile_lock._redis", redis_client):
            self.assertTrue(is_profile_lock_held("whatsapp"))
        redis_client.exists.assert_called_once_with("profile_lock:whatsapp")

    def test_acquire_uses_the_account_specific_message(self):
        lock = unittest.mock.Mock()
        lock.acquire.return_value = False
        redis_client = unittest.mock.Mock()
        redis_client.lock.return_value = lock
        with patch("worker.profile_lock._redis", redis_client):
            with self.assertRaises(ProfileInUseError) as raised:
                acquire_profile_lock("whatsapp", blocking_timeout=0)
        self.assertNotIn("LinkedIn", str(raised.exception))
        self.assertIn("WhatsApp", str(raised.exception))


# Imported after the class so the mock path above stays obvious.
import unittest.mock  # noqa: E402


if __name__ == "__main__":
    unittest.main()

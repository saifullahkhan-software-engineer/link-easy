"""Regression tests for explicit WhatsApp account disconnection.

Per-user rollout: disconnect only ever touches the caller's own session row,
its per-session browsers, and its per-session profile directory.
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import api.v1.whatsapp_scanner as scanner  # noqa: E402


class _Database:
    def __init__(self):
        self.statements = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self):
        self.commits += 1


class WhatsAppDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_stops_browsers_clears_db_and_removes_profile(self):
        db = _Database()
        profile_lock = object()

        with tempfile.TemporaryDirectory() as root:
            profile = Path(root) / "whatsapp" / "session-1"
            profile.mkdir(parents=True)
            (profile / "IndexedDB-device-key").write_text("secret")

            session_row = SimpleNamespace(
                id=1,
                status="connected",
                is_active=True,
                owner_email="owner@test.dev",
                profile_dir=str(profile),
                cookies_json=None,
                storage_state_json=None,
                created_at=None,
                updated_at=None,
            )
            live = SimpleNamespace(stop=AsyncMock())
            view = SimpleNamespace(stop=AsyncMock())

            with (
                patch(
                    "api.v1.whatsapp_scanner.get_owned_session",
                    AsyncMock(return_value=session_row),
                ),
                patch("services.browser_view.get_browser_view", return_value=view),
                patch(
                    "services.whatsapp_live_browser.get_live_browser",
                    return_value=live,
                ),
                patch(
                    "worker.profile_lock.acquire_profile_lock",
                    return_value=profile_lock,
                ),
                patch("worker.profile_lock.release_profile_lock") as release,
            ):
                response = await scanner.disconnect_whatsapp(
                    SimpleNamespace(email="owner@test.dev"), db
                )

            self.assertEqual(response.status, "disconnected")
            self.assertFalse(profile.exists())

        live.stop.assert_awaited_once()
        view.stop.assert_awaited_once()
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(db.statements), 1)
        compiled_values = db.statements[0].compile().params
        self.assertEqual(compiled_values["status"], "disconnected")
        self.assertFalse(compiled_values["is_active"])
        self.assertIsNone(compiled_values["cookies_json"])
        self.assertIsNone(compiled_values["storage_state_json"])
        release.assert_called_once_with(profile_lock)

    async def test_disconnect_is_a_noop_without_a_session(self):
        db = _Database()

        with (
            patch(
                "api.v1.whatsapp_scanner.get_owned_session",
                AsyncMock(return_value=None),
            ),
        ):
            response = await scanner.disconnect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "disconnected")
        self.assertEqual(db.commits, 0)


if __name__ == "__main__":
    unittest.main()

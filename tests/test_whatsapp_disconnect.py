"""Regression tests for explicit WhatsApp account disconnection."""
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

from api.v1.whatsapp_scanner import disconnect_whatsapp  # noqa: E402


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
        from services.browser_view import browser_view
        from services.whatsapp_live_browser import live_browser

        db = _Database()
        profile_lock = object()

        with tempfile.TemporaryDirectory() as root:
            profile = Path(root) / "whatsapp"
            profile.mkdir()
            (profile / "IndexedDB-device-key").write_text("secret")

            with (
                patch.object(live_browser, "stop", AsyncMock()) as stop_live,
                patch.object(browser_view, "stop", AsyncMock()) as stop_view,
                patch(
                    "worker.profile_lock.acquire_profile_lock",
                    return_value=profile_lock,
                ),
                patch("worker.profile_lock.release_profile_lock") as release,
                patch(
                    "services.whatsapp_browser.whatsapp_profile_dir",
                    return_value=str(profile),
                ),
            ):
                response = await disconnect_whatsapp(
                    SimpleNamespace(email="owner@test.dev"), db
                )

            self.assertEqual(response.status, "disconnected")
            self.assertFalse(profile.exists())

        stop_live.assert_awaited_once()
        stop_view.assert_awaited_once()
        self.assertEqual(db.commits, 1)
        self.assertEqual(len(db.statements), 1)
        compiled_values = db.statements[0].compile().params
        self.assertEqual(compiled_values["status"], "disconnected")
        self.assertFalse(compiled_values["is_active"])
        self.assertIsNone(compiled_values["cookies_json"])
        self.assertIsNone(compiled_values["storage_state_json"])
        release.assert_called_once_with(profile_lock)


if __name__ == "__main__":
    unittest.main()

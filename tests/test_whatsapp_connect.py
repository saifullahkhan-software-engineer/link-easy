"""WhatsApp Connect must start the browser view and surface exact errors.

Per-user rollout: connect resolves the caller's own session row first and
drives that session's managers — never a process-wide singleton. These tests
patch the resolution helpers accordingly.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from fastapi import HTTPException  # noqa: E402

import api.v1.whatsapp_scanner as scanner  # noqa: E402
from models.whatsapp import WhatsAppSession  # noqa: E402


class _Database:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.commits = 0

    async def execute(self, _statement):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: self._rows[0] if self._rows else None))

    async def commit(self):
        self.commits += 1

    def add(self, row):
        if getattr(row, "id", None) is None:
            row.id = 11
        self._rows.insert(0, row)

    async def flush(self):
        for row in self._rows:
            if getattr(row, "id", None) is None:
                row.id = 11

    async def refresh(self, row):
        if getattr(row, "id", None) is None:
            row.id = 11


def _session_row(session_id=11):
    return SimpleNamespace(
        id=session_id,
        status="waiting_qr",
        is_active=True,
        owner_email="owner@test.dev",
        profile_dir=f"/tmp/profiles/whatsapp/session-{session_id}",
        cookies_json=None,
        storage_state_json=None,
        created_at=None,
        updated_at=None,
    )


class WhatsAppConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_success_starts_browser_and_watcher(self):
        db = _Database()
        started = {}

        async def _start(url):
            started["url"] = url
            return {"status": "running", "error": None, "message": "ready"}

        view = SimpleNamespace(
            status="idle",
            last_error=None,
            stop=AsyncMock(),
            ensure_started=AsyncMock(side_effect=_start),
            snapshot=lambda: {"status": "running", "error": None},
        )
        live = SimpleNamespace(status="idle", stop=AsyncMock())

        with (
            patch(
                "api.v1.whatsapp_sessions.get_owned_session",
                AsyncMock(return_value=_session_row()),
            ),
            patch("services.browser_view.get_browser_view", return_value=view),
            patch("services.whatsapp_live_browser.get_live_browser", return_value=live),
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher") as spawn,
        ):
            response = await scanner.connect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "waiting_qr")
        self.assertEqual(started["url"], "https://web.whatsapp.com/")
        spawn.assert_called_once()

    async def test_connect_creates_owned_session_with_profile_dir(self):
        """A first-time user gets their own session row + per-session profile dir."""
        db = _Database()
        created = {}

        def _add(row):
            created["row"] = row
            row.id = 11
            db._rows.insert(0, row)

        view = SimpleNamespace(
            status="idle",
            last_error=None,
            stop=AsyncMock(),
            ensure_started=AsyncMock(
                return_value={"status": "running", "error": None, "message": "ready"}
            ),
            snapshot=lambda: {"status": "running", "error": None},
        )
        live = SimpleNamespace(status="idle", stop=AsyncMock())

        with (
            patch("api.v1.whatsapp_sessions.get_owned_session", AsyncMock(return_value=None)),
            patch.object(db, "add", side_effect=_add),
            patch("services.browser_view.get_browser_view", return_value=view),
            patch("services.whatsapp_live_browser.get_live_browser", return_value=live),
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher"),
        ):
            response = await scanner.connect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "waiting_qr")
        row = created["row"]
        self.assertIsInstance(row, WhatsAppSession)
        self.assertEqual(row.owner_email, "owner@test.dev")
        self.assertTrue(row.profile_dir.endswith("/whatsapp/session-11"))

    async def test_browser_view_startup_failure_surfaces_exact_error(self):
        db = _Database()
        view = SimpleNamespace(
            status="idle",
            last_error=None,
            stop=AsyncMock(),
            ensure_started=AsyncMock(
                return_value={
                    "status": "error",
                    "error": "chromium failed: missing browser binary",
                    "message": "Failed to start browser view",
                }
            ),
            snapshot=lambda: {"status": "error", "error": "chromium failed: missing browser binary"},
        )
        live = SimpleNamespace(status="idle", stop=AsyncMock())

        with (
            patch(
                "api.v1.whatsapp_sessions.get_owned_session",
                AsyncMock(return_value=_session_row()),
            ),
            patch("services.browser_view.get_browser_view", return_value=view),
            patch("services.whatsapp_live_browser.get_live_browser", return_value=live),
        ):
            with self.assertRaises(HTTPException) as ctx:
                await scanner.connect_whatsapp(
                    SimpleNamespace(email="owner@test.dev"), db
                )

        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("chromium failed: missing browser binary", ctx.exception.detail)
        self.assertNotEqual(ctx.exception.status_code, 401)

    async def test_starting_browser_is_waited_for(self):
        db = _Database()
        snapshots = [
            {"status": "starting", "error": None, "message": "Launching"},
            {"status": "starting", "error": None, "message": "Launching"},
            {"status": "running", "error": None, "message": "ready"},
        ]

        view = SimpleNamespace(
            status="starting",
            last_error=None,
            stop=AsyncMock(),
            ensure_started=AsyncMock(return_value=snapshots[0]),
            snapshot=lambda: snapshots.pop(0) if snapshots else {"status": "running", "error": None},
        )
        live = SimpleNamespace(status="idle", stop=AsyncMock())

        with (
            patch(
                "api.v1.whatsapp_sessions.get_owned_session",
                AsyncMock(return_value=_session_row()),
            ),
            patch("services.browser_view.get_browser_view", return_value=view),
            patch("services.whatsapp_live_browser.get_live_browser", return_value=live),
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher"),
        ):
            response = await scanner.connect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "waiting_qr")


if __name__ == "__main__":
    unittest.main()

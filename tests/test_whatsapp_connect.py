"""WhatsApp Connect must start the browser view and surface exact errors."""
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

    async def refresh(self, row):
        if getattr(row, "id", None) is None:
            row.id = 11


class WhatsAppConnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_success_starts_browser_and_watcher(self):
        db = _Database()
        started = {}

        async def _start(url):
            started["url"] = url
            return {"status": "running", "error": None, "message": "ready"}

        with (
            patch("services.whatsapp_live_browser.live_browser", SimpleNamespace(status="idle", stop=AsyncMock())),
            patch("services.browser_view.browser_view") as view,
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher") as spawn,
        ):
            view.status = "idle"
            view.last_error = None
            view.stop = AsyncMock()
            view.ensure_started = AsyncMock(side_effect=_start)
            view.snapshot = lambda: {"status": "running", "error": None}

            # connect_whatsapp imports browser_view inside the function.
            import services.browser_view as bv

            with patch.object(bv, "browser_view", view), patch.object(
                bv, "WHATSAPP_URL", "https://web.whatsapp.com/"
            ):
                response = await scanner.connect_whatsapp(
                    SimpleNamespace(email="owner@test.dev"), db
                )

        self.assertEqual(response.status, "waiting_qr")
        self.assertEqual(started["url"], "https://web.whatsapp.com/")
        spawn.assert_called_once()

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

        import services.browser_view as bv
        import services.whatsapp_live_browser as live

        with (
            patch.object(live, "live_browser", SimpleNamespace(status="idle", stop=AsyncMock())),
            patch.object(bv, "browser_view", view),
            patch.object(bv, "WHATSAPP_URL", "https://web.whatsapp.com/"),
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

        import services.browser_view as bv
        import services.whatsapp_live_browser as live

        with (
            patch.object(live, "live_browser", SimpleNamespace(status="idle", stop=AsyncMock())),
            patch.object(bv, "browser_view", view),
            patch.object(bv, "WHATSAPP_URL", "https://web.whatsapp.com/"),
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher"),
        ):
            response = await scanner.connect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "waiting_qr")


if __name__ == "__main__":
    unittest.main()

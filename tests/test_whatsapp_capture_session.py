"""Tests for the manual "capture session" WhatsApp escape hatch.

WhatsApp Web changes its DOM regularly, so the automatic QR watcher sometimes
misses a successful scan and the UI stays stuck on "waiting_qr". The connect
page therefore offers a button that snapshots the live browser session on
demand; these tests cover the endpoint behind it.
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import HTTPException  # noqa: E402

import api.v1.whatsapp_scanner as scanner  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Database:
    """Minimal async DB stub returning a single session row."""

    def __init__(self, rows):
        self._rows = rows
        self.committed = 0

    async def execute(self, _statement):
        return _Rows(self._rows)

    def add(self, row):
        self._rows.insert(0, row)

    async def flush(self):
        for row in self._rows:
            if getattr(row, "id", None) is None:
                row.id = 1

    async def commit(self):
        self.committed += 1

    async def refresh(self, _row):
        return None


def _session_row(status="waiting_qr"):
    return SimpleNamespace(
        id=7,
        status=status,
        is_active=True,
        cookies_json=None,
        storage_state_json=None,
        created_at=None,
        updated_at=None,
    )


class _BrowserView:
    def __init__(self, status="running", page=object(), context=object()):
        self.status = status
        self.page = page
        self.context = context
        self.stopped = False

    async def stop(self):
        self.stopped = True
        return {"status": "idle"}


STORAGE_STATE = {"cookies": [{"name": "wa", "value": "1"}], "origins": []}


class CaptureSessionTests(unittest.IsolatedAsyncioTestCase):
    def _patch_browser(self, view):
        # Per-user rollout: the capture endpoint resolves the caller's own
        # session view through get_browser_view(session_id).
        return patch.dict(
            sys.modules,
            {
                "services.browser_view": SimpleNamespace(
                    browser_view=view,
                    WHATSAPP_URL="x",
                    get_browser_view=lambda _session_id: view,
                )
            },
        )

    def _patch_whatsapp_browser(self, *, logged_in, showing_qr=False):
        module = SimpleNamespace(
            get_storage_state=AsyncMock(return_value=STORAGE_STATE),
            is_logged_in=AsyncMock(return_value=logged_in),
            is_showing_qr=AsyncMock(return_value=showing_qr),
            wait_for_login=AsyncMock(return_value=logged_in),
            whatsapp_profile_dir=lambda: "/tmp/profile",
        )
        return patch.dict(sys.modules, {"services.whatsapp_browser": module})

    async def test_capture_marks_session_connected_and_stops_browser(self):
        row = _session_row()
        db = _Database([row])
        view = _BrowserView()

        with self._patch_browser(view), self._patch_whatsapp_browser(logged_in=True):
            response = await scanner.capture_whatsapp_session(
                False, SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "connected")
        self.assertTrue(response.detected)
        self.assertEqual(row.status, "connected")
        self.assertTrue(row.is_active)
        self.assertEqual(row.storage_state_json, STORAGE_STATE)
        self.assertEqual(row.cookies_json, STORAGE_STATE["cookies"])
        self.assertTrue(view.stopped, "browser view should be released after capture")

    async def test_capture_rejected_while_qr_is_still_on_screen(self):
        db = _Database([_session_row()])
        view = _BrowserView()

        with self._patch_browser(view), self._patch_whatsapp_browser(
            logged_in=False, showing_qr=True
        ):
            with self.assertRaises(HTTPException) as ctx:
                await scanner.capture_whatsapp_session(
                    False, SimpleNamespace(email="owner@test.dev"), db
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("still showing the QR code", ctx.exception.detail)

    async def test_force_captures_even_when_detection_fails(self):
        row = _session_row()
        db = _Database([row])
        view = _BrowserView()

        with self._patch_browser(view), self._patch_whatsapp_browser(
            logged_in=False, showing_qr=True
        ):
            response = await scanner.capture_whatsapp_session(
                True, SimpleNamespace(email="owner@test.dev"), db
            )

        self.assertEqual(response.status, "connected")
        self.assertFalse(response.detected)
        self.assertEqual(row.status, "connected")

    async def test_capture_requires_an_open_browser(self):
        db = _Database([_session_row()])
        view = _BrowserView(status="idle", page=None, context=None)

        with self._patch_browser(view), self._patch_whatsapp_browser(logged_in=True):
            with self.assertRaises(HTTPException) as ctx:
                await scanner.capture_whatsapp_session(
                    False, SimpleNamespace(email="owner@test.dev"), db
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("browser is not open", ctx.exception.detail)

    async def test_capture_rejects_empty_storage_state(self):
        db = _Database([_session_row()])
        view = _BrowserView()
        module = SimpleNamespace(
            get_storage_state=AsyncMock(return_value={"cookies": [], "origins": []}),
            is_logged_in=AsyncMock(return_value=True),
            is_showing_qr=AsyncMock(return_value=False),
            wait_for_login=AsyncMock(return_value=True),
        )

        with self._patch_browser(view), patch.dict(
            sys.modules, {"services.whatsapp_browser": module}
        ):
            with self.assertRaises(HTTPException) as ctx:
                await scanner.capture_whatsapp_session(
                    False, SimpleNamespace(email="owner@test.dev"), db
                )

        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("no WhatsApp session data", ctx.exception.detail)

    async def test_capture_cancels_the_running_qr_watcher(self):
        import asyncio

        row = _session_row()
        db = _Database([row])
        view = _BrowserView()

        async def _never():
            await asyncio.sleep(300)

        task = asyncio.create_task(_never())
        scanner._active_watchers[row.id] = task
        try:
            with self._patch_browser(view), self._patch_whatsapp_browser(logged_in=True):
                await scanner.capture_whatsapp_session(
                    False, SimpleNamespace(email="owner@test.dev"), db
                )
            self.assertNotIn(row.id, scanner._active_watchers)
            self.assertTrue(task.cancelled() or task.cancelling())
        finally:
            task.cancel()
            scanner._active_watchers.pop(row.id, None)


if __name__ == "__main__":
    unittest.main()

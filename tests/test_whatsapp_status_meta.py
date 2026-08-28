"""Regression tests for WhatsApp connection metadata on the status endpoint.

The manage-WhatsApp card mirrors the LinkedIn account card, which shows
"Added" / "Last updated". Those values come from the session row's
``created_at`` / ``updated_at``, so the status endpoint must expose them.

Also covers the "honest status" field: ``reconnect_required`` is true when
the DB row says "connected" but the durable Chromium profile directory is
missing/empty (e.g. a deploy without the /app/profiles volume wiped it).
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from api.v1.whatsapp_scanner import get_whatsapp_status  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Database:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _statement):
        return _Rows(self._rows)


class WhatsAppStatusMetaTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_session_reports_disconnected_without_dates(self):
        db = _Database([])
        response = await get_whatsapp_status(
            SimpleNamespace(email="owner@test.dev"), db
        )
        self.assertEqual(response.status, "disconnected")
        self.assertFalse(response.is_active)
        self.assertIsNone(response.created_at)
        self.assertIsNone(response.updated_at)

    async def test_connected_session_carries_added_and_updated_dates(self):
        added = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
        updated = datetime(2026, 8, 10, 12, 30, tzinfo=timezone.utc)
        session = SimpleNamespace(
            status="connected", is_active=True, created_at=added, updated_at=updated
        )
        db = _Database([session])

        response = await get_whatsapp_status(
            SimpleNamespace(email="owner@test.dev"), db
        )
        self.assertEqual(response.status, "connected")
        self.assertTrue(response.is_active)
        self.assertEqual(response.created_at, added)
        self.assertEqual(response.updated_at, updated)

    # ── honest status: DB says connected, but the profile dir was wiped ────

    async def test_connected_with_profile_present_does_not_require_reconnect(self):
        session = SimpleNamespace(
            status="connected", is_active=True, created_at=None, updated_at=None
        )
        db = _Database([session])
        with tempfile.TemporaryDirectory() as tmp:
            # A connected profile always contains at least one file.
            with open(os.path.join(tmp, "Cookies"), "w") as fh:
                fh.write("{}")
            with mock.patch(
                "services.whatsapp_browser.whatsapp_profile_dir", return_value=tmp
            ):
                response = await get_whatsapp_status(
                    SimpleNamespace(email="owner@test.dev"), db
                )
        self.assertEqual(response.status, "connected")
        self.assertFalse(response.reconnect_required)

    async def test_connected_with_missing_profile_requires_reconnect(self):
        session = SimpleNamespace(
            status="connected", is_active=True, created_at=None, updated_at=None
        )
        db = _Database([session])
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "profiles", "whatsapp")  # never created
            with mock.patch(
                "services.whatsapp_browser.whatsapp_profile_dir", return_value=missing
            ):
                response = await get_whatsapp_status(
                    SimpleNamespace(email="owner@test.dev"), db
                )
        # The DB state is reported unchanged (the endpoint is read-only);
        # the flag tells the UI not to trust the green.
        self.assertEqual(response.status, "connected")
        self.assertTrue(response.reconnect_required)

    async def test_connected_with_empty_profile_dir_requires_reconnect(self):
        session = SimpleNamespace(
            status="connected", is_active=True, created_at=None, updated_at=None
        )
        db = _Database([session])
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch(
                "services.whatsapp_browser.whatsapp_profile_dir", return_value=tmp
            ):
                response = await get_whatsapp_status(
                    SimpleNamespace(email="owner@test.dev"), db
                )
        self.assertTrue(response.reconnect_required)

    async def test_disconnected_never_requires_reconnect(self):
        session = SimpleNamespace(
            status="disconnected", is_active=False, created_at=None, updated_at=None
        )
        db = _Database([session])
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "profiles", "whatsapp")  # never created
            with mock.patch(
                "services.whatsapp_browser.whatsapp_profile_dir", return_value=missing
            ):
                response = await get_whatsapp_status(
                    SimpleNamespace(email="owner@test.dev"), db
                )
        self.assertEqual(response.status, "disconnected")
        self.assertFalse(response.reconnect_required)


if __name__ == "__main__":
    unittest.main()

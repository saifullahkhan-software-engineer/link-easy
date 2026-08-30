"""Per-user WhatsApp sessions: ownership, profile dirs, locks, and isolation.

The per-user rollout mirrors the LinkedIn accounts model: every WhatsApp
connection belongs to one platform user, gets its own durable Chromium
profile directory, its own Redis profile-lock key, and its own in-process
browser managers — so ten users can drive ten WhatsApp numbers concurrently.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
# worker.celery_app validates the credential key at import time.
_CRED_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", _CRED_KEY)

from api.v1.whatsapp_sessions import (  # noqa: E402
    get_owned_session,
    session_profile_dir,
)
from models.whatsapp import WhatsAppSession  # noqa: E402
from services.whatsapp_browser import (  # noqa: E402
    whatsapp_lock_id,
    whatsapp_profile_dir,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _Database:
    """Fake async DB that honors owner_email filters on WhatsAppSession."""

    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.commits = 0

    async def execute(self, statement):
        # Emulate the WHERE clauses the resolver really sends:
        #   owner_email = :owner_email_1  → equality with a bound value
        #   owner_email IS NULL           → only unowned rows
        rows = self._rows
        criteria = list(getattr(statement, "_where_criteria", None) or ())
        for crit in criteria:
            text = str(crit)
            if "owner_email" not in text:
                continue
            if "IS NULL" in text.upper():
                rows = [r for r in rows if getattr(r, "owner_email", None) is None]
            else:
                params = {}
                try:
                    params = statement.compile().params
                except Exception:
                    pass
                if params:
                    value = next(iter(params.values()))
                    rows = [
                        r for r in rows if getattr(r, "owner_email", None) == value
                    ]
        return _Rows(rows)

    async def commit(self):
        self.commits += 1

    async def refresh(self, row):
        return None


def _session(session_id, owner=None, status="connected"):
    return SimpleNamespace(
        id=session_id,
        owner_email=owner,
        profile_dir=None,
        status=status,
        is_active=True,
        cookies_json=None,
        storage_state_json=None,
        created_at=None,
        updated_at=None,
    )


class ProfileDirAndLockTests(unittest.TestCase):
    def test_profile_dirs_are_per_session(self):
        self.assertEqual(
            whatsapp_profile_dir(1), whatsapp_profile_dir(1)
        )
        self.assertNotEqual(
            whatsapp_profile_dir(1), whatsapp_profile_dir(2)
        )
        self.assertIn("/whatsapp/session-1", whatsapp_profile_dir(1))
        self.assertIn("/whatsapp/session-2", whatsapp_profile_dir(2))
        # Legacy (no id) keeps the shared flat directory.
        self.assertNotIn("session-", whatsapp_profile_dir(None))
        self.assertTrue(whatsapp_profile_dir(None).endswith("/whatsapp"))

    def test_lock_keys_are_per_session(self):
        self.assertEqual(whatsapp_lock_id(1), "whatsapp:1")
        self.assertNotEqual(whatsapp_lock_id(1), whatsapp_lock_id(2))
        self.assertEqual(whatsapp_lock_id(None), "whatsapp")

    def test_session_profile_dir_prefers_explicit_column(self):
        row = _session(3, owner="a@test.dev")
        row.profile_dir = "/custom/wa-3"
        self.assertEqual(session_profile_dir(row), "/custom/wa-3")
        row.profile_dir = None
        self.assertTrue(session_profile_dir(row).endswith("/whatsapp"))


class SessionResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_scoped_lookup_returns_the_callers_session(self):
        mine = _session(1, owner="a@test.dev")
        theirs = _session(2, owner="b@test.dev")
        db = _Database([mine, theirs])

        session = await get_owned_session(
            db, SimpleNamespace(email="a@test.dev")
        )
        self.assertIs(session, mine)

    async def test_first_user_adopts_the_legacy_unowned_row(self):
        legacy = _session(9, owner=None, status="connected")
        db = _Database([legacy])

        session = await get_owned_session(
            db, SimpleNamespace(email="first@test.dev")
        )
        self.assertIs(session, legacy)
        self.assertEqual(legacy.owner_email, "first@test.dev")
        self.assertEqual(db.commits, 1)

    async def test_second_user_never_sees_the_adopted_session(self):
        adopted = _session(9, owner="first@test.dev", status="connected")
        db = _Database([adopted])

        session = await get_owned_session(
            db, SimpleNamespace(email="second@test.dev")
        )
        self.assertIsNone(session)
        # The adopted row must not be re-claimed by a different user.
        self.assertEqual(adopted.owner_email, "first@test.dev")

    async def test_require_connected_rejects_missing_or_broken_sessions(self):
        from fastapi import HTTPException

        # No session at all.
        with self.assertRaises(HTTPException) as ctx:
            await get_owned_session(
                _Database([]), SimpleNamespace(email="a@test.dev"),
                require_connected=True,
            )
        self.assertEqual(ctx.exception.status_code, 400)

        # Session exists but is disconnected.
        disconnected = _session(1, owner="a@test.dev", status="disconnected")
        with self.assertRaises(HTTPException):
            await get_owned_session(
                _Database([disconnected]), SimpleNamespace(email="a@test.dev"),
                require_connected=True,
            )


class PerSessionManagerTests(unittest.TestCase):
    def test_browser_views_are_registered_per_session(self):
        from services.browser_view import get_browser_view

        legacy = get_browser_view(None)
        one = get_browser_view(1)
        two = get_browser_view(2)

        self.assertIsNot(one, two)
        self.assertIsNot(one, legacy)
        self.assertEqual(one.session_id, 1)
        self.assertEqual(two.session_id, 2)
        self.assertIs(get_browser_view(1), one, "registry must reuse instances")

    def test_live_browsers_are_registered_per_session(self):
        from services.whatsapp_live_browser import get_live_browser

        legacy = get_live_browser(None)
        one = get_live_browser(1)
        two = get_live_browser(2)

        self.assertIsNot(one, two)
        self.assertIsNot(one, legacy)
        self.assertEqual(one.session_id, 1)
        self.assertEqual(two.session_id, 2)
        self.assertIs(get_live_browser(1), one, "registry must reuse instances")


class WorkerSessionResolutionTests(unittest.TestCase):
    """The scanner task must open the filter owner's session, not anyone else's."""

    def _run_scan_with_sessions(self, sessions, filter_owner):
        import asyncio
        from contextlib import nullcontext

        import worker.tasks.whatsapp_tasks as tasks

        class _FakeQuery:
            def __init__(self, rows):
                self._rows = rows

            def filter(self, *args, **kwargs):
                return self

            def order_by(self, *args, **kwargs):
                return self

            def first(self):
                return self._rows[0] if self._rows else None

            def all(self):
                return list(self._rows)

        class _FakeDb:
            def __init__(self):
                self.queries = []

            def query(self, model):
                if model.__name__ == "WhatsAppSession":
                    # Emulate the task's owner-scoped lookup.
                    rows = [
                        r
                        for r in sessions
                        if r.status == "connected"
                        and r.is_active
                        and getattr(r, "owner_email", None) == filter_owner
                    ]
                    return _FakeQuery(rows)
                if model.__name__ == "WhatsAppScanFilter":
                    return _FakeQuery(
                        [
                            SimpleNamespace(
                                id=42,
                                status="active",
                                owner_email=filter_owner,
                                match_threshold=60.0,
                                keywords=None,
                                role=None,
                                job_title=None,
                                experience_level=None,
                                latest_messages_limit=20,
                                next_scan_at=None,
                                last_scan_at=None,
                                remaining_seconds=None,
                                interval_hours=1.0,
                            )
                        ]
                    )
                if model.__name__ == "WhatsAppMonitoredGroup":
                    return _FakeQuery(
                        [
                            SimpleNamespace(
                                id=1,
                                group_name="Jobs Group",
                                whatsapp_id="g-1",
                                last_message_id=None,
                                last_message_timestamp=None,
                                filter_id=42,
                            )
                        ]
                    )
                return _FakeQuery([])

        launched = {}

        async def fake_launch(headless=True, profile_dir=None):
            launched["profile_dir"] = profile_dir
            # Stop after launch — only the selected profile dir matters here.
            raise RuntimeError("stop after launch")

        with (
            patch(
                "worker.tasks.whatsapp_tasks.get_sync_db",
                new=lambda: nullcontext(_FakeDb()),
            ),
            patch(
                "services.whatsapp_browser.launch_whatsapp_persistent",
                new=AsyncMock(side_effect=fake_launch),
            ),
            patch("core.profiles.profile_dir_missing", return_value=False),
            patch("worker.profile_lock.acquire_profile_lock", return_value="lock"),
            patch("worker.profile_lock.release_profile_lock"),
        ):
            asyncio.run(tasks._check_whatsapp_messages_async(42))

        return launched

    def test_scan_opens_the_filter_owners_session_profile(self):
        owner_a = _session(1, owner="a@test.dev", status="connected")
        owner_b = _session(2, owner="b@test.dev", status="connected")
        owner_a.profile_dir = "/profiles/wa/session-1"
        owner_b.profile_dir = "/profiles/wa/session-2"

        launched = self._run_scan_with_sessions(
            [owner_a, owner_b], filter_owner="a@test.dev"
        )
        self.assertEqual(launched["profile_dir"], "/profiles/wa/session-1")

    def test_scan_skips_when_filter_owner_has_no_connected_session(self):
        owner_b = _session(2, owner="b@test.dev", status="connected")
        owner_b.profile_dir = "/profiles/wa/session-2"

        import asyncio

        import worker.tasks.whatsapp_tasks as tasks

        class _FakeDb:
            def query(self, model):
                if model.__name__ == "WhatsAppScanFilter":
                    rows = [
                        SimpleNamespace(
                            id=42,
                            status="active",
                            owner_email="a@test.dev",
                            match_threshold=60.0,
                            keywords=None,
                            role=None,
                            job_title=None,
                            experience_level=None,
                            latest_messages_limit=20,
                            next_scan_at=None,
                            last_scan_at=None,
                            remaining_seconds=None,
                            interval_hours=1.0,
                        )
                    ]
                    return type(
                        "Q",
                        (),
                        {
                            "filter": lambda self, *a, **k: self,
                            "order_by": lambda self, *a, **k: self,
                            "first": lambda self: rows[0],
                        },
                    )()
                # No session for the filter owner → empty list.
                return type(
                    "Q",
                    (),
                    {
                        "filter": lambda self, *a, **k: self,
                        "order_by": lambda self, *a, **k: self,
                        "first": lambda self: None,
                    },
                )()

        with patch(
            "worker.tasks.whatsapp_tasks.get_sync_db",
            new=lambda: __import__("contextlib").nullcontext(_FakeDb()),
        ):
            result = asyncio.run(tasks._check_whatsapp_messages_async(42))

        self.assertEqual(result["status"], "skipped")
        self.assertIn("no connected session", result["reason"])


if __name__ == "__main__":
    unittest.main()

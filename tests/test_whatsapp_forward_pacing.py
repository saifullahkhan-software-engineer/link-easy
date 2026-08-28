"""Regression tests: WhatsApp forward pacing (anti-blocking filter).

WhatsApp errors out when several matched messages are forwarded back-to-back
(simultaneous sends trip its spam/blocking filter). The scanner therefore
waits ``WHATSAPP_FORWARD_DELAY_SECONDS`` (default 10s) between every
consecutive forward inside a scan run. These tests drive the real
``_check_whatsapp_messages_async`` task with a scripted fake DB session and
assert the pacing sleeps happen between forwards.
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

_required_env = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "JWT_SECRET": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "PASSWORD_RESET_URL": "http://localhost/reset",
    "BACKEND_CORS_ORIGINS": "http://localhost:5173",
    "RESEND_API_KEY": "test",
    "FROM_EMAIL": "test@example.com",
    "REDIS_URL": "redis://localhost:6379/0",
}
for _key, _value in _required_env.items():
    os.environ.setdefault(_key, _value)

# Other test modules may have set CREDENTIAL_ENCRYPTION_KEY to a short test
# value before Settings was constructed. Force the env AND the constructed
# settings object to a valid 32-byte key so worker.celery_app's startup
# validation (validate_encryption_key) does not fail when we import it.
_CRED_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
os.environ["CREDENTIAL_ENCRYPTION_KEY"] = _CRED_KEY

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import settings  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = _CRED_KEY

from models.whatsapp import (  # noqa: E402
    WhatsAppForwardGroup,
    WhatsAppMonitoredGroup,
    WhatsAppRawMessage,
    WhatsAppScanFilter,
    WhatsAppSession,
)
import worker.tasks.whatsapp_tasks as whatsapp_tasks  # noqa: E402


# ── Fake ORM session (scripted, no DB) ───────────────────────────────────────


class _FakeQuery:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._rows = self._rows[:n]
        return self

    def offset(self, n):
        self._rows = self._rows[n:]
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Minimal fake session supporting the query chains the task uses.

    ``query(Model)`` returns the seeded row list for that model (rows are
    shared objects, so in-place status mutations are visible to later
    queries). ``query(Model.column)`` returns canned column tuples used for
    the dedupe lookup. ``add()`` appends scraped raw messages so the
    OCR/scoring pass picks them up, mirroring the real DB flow.
    """

    def __init__(self, rows_by_model, column_rows=None):
        self._rows = {t.__name__: list(rows) for t, rows in rows_by_model.items()}
        self._column_rows = {
            t.__name__: list(rows) for t, rows in (column_rows or {}).items()
        }

    def query(self, model):
        if isinstance(model, type):
            return _FakeQuery(self._rows.get(model.__name__, []))
        # InstrumentedAttribute, e.g. WhatsAppRawMessage.whatsapp_message_id
        return _FakeQuery(self._column_rows.get(model.class_.__name__, []))

    def add(self, obj):
        if isinstance(obj, WhatsAppRawMessage):
            self._rows.setdefault("WhatsAppRawMessage", []).append(obj)

    def commit(self):
        pass

    def close(self):
        pass

    def refresh(self, obj):
        pass


def _build_session():
    session_row = SimpleNamespace(id=7, status="connected", is_active=True)
    filter_row = SimpleNamespace(
        id=42,
        status="active",
        match_threshold=60.0,
        keywords=["engineer"],
        role=None,
        job_title=None,
        experience_level=None,
        latest_messages_limit=20,
        last_scan_at=None,
        next_scan_at=None,
        remaining_seconds=None,
        interval_hours=1.0,
    )
    monitored_row = SimpleNamespace(
        id=1,
        group_name="Jobs Group",
        whatsapp_id="g-1",
        last_message_id=None,
        last_message_timestamp=None,
        filter_id=42,
    )
    forward_row = SimpleNamespace(
        id=2,
        group_name="Forward Group",
        whatsapp_id="g-2",
        filter_id=42,
    )
    return _FakeSession(
        rows_by_model={
            WhatsAppSession: [session_row],
            WhatsAppScanFilter: [filter_row],
            WhatsAppMonitoredGroup: [monitored_row],
            WhatsAppForwardGroup: [forward_row],
            WhatsAppRawMessage: [],
        },
        # Nothing in the dedupe lookup matches the scraped ids -> all unseen.
        column_rows={WhatsAppRawMessage: [("already-pulled",)]},
    )


def _scraped_messages(count):
    return [
        {
            "whatsapp_message_id": f"wa-{i}",
            "sender_name": f"Sender {i}",
            "message_text": f"Senior engineer opening #{i}",
            "message_type": "text",
            "timestamp": 1000 + i,
        }
        for i in range(1, count + 1)
    ]


class WhatsAppForwardPacingTests(unittest.TestCase):
    def _run_scan(self, scraped_count, forward_delay):
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        fake_session = _build_session()
        page = Mock()

        # The seeded session row is "connected", which in production implies
        # an intact on-disk profile. Give the task's honest-status check the
        # same world the mocked browser layer assumes.
        profile_tmp = tempfile.mkdtemp(prefix="wa-profile-")
        self.addCleanup(shutil.rmtree, profile_tmp, ignore_errors=True)
        with open(os.path.join(profile_tmp, "Cookies"), "w") as fh:
            fh.write("{}")

        with patch(
            "services.whatsapp_browser.whatsapp_profile_dir",
            new=Mock(return_value=profile_tmp),
        ), patch(
            "services.whatsapp_browser.launch_whatsapp_persistent",
            new=AsyncMock(return_value=("pw", "ctx", page)),
        ), patch(
            "services.whatsapp_browser.navigate_to_whatsapp", new=AsyncMock()
        ), patch(
            "services.whatsapp_browser.wait_for_login", new=AsyncMock(return_value=True)
        ), patch(
            "services.whatsapp_browser.navigate_to_group",
            new=AsyncMock(return_value=True),
        ), patch(
            "services.whatsapp_browser.scrape_messages_from_current_chat",
            new=AsyncMock(return_value=_scraped_messages(scraped_count)),
        ), patch(
            "services.whatsapp_browser.forward_message_to_group",
            new=AsyncMock(return_value=True),
        ) as fwd, patch(
            "services.whatsapp_browser.safe_close", new=AsyncMock()
        ), patch(
            "services.whatsapp_matcher.compute_match_score", new=Mock(return_value=80.0)
        ), patch(
            "worker.profile_lock.acquire_profile_lock", new=Mock(return_value="lock")
        ), patch(
            "worker.profile_lock.release_profile_lock", new=Mock()
        ), patch(
            "worker.tasks.whatsapp_tasks.get_sync_db",
            new=lambda: nullcontext(fake_session),
        ), patch(
            "worker.tasks.whatsapp_tasks.asyncio.sleep", new=fake_sleep
        ), patch.object(
            whatsapp_tasks, "FORWARD_DELAY_SECONDS", forward_delay
        ):
            result = asyncio.run(whatsapp_tasks._check_whatsapp_messages_async(42))

        return result, sleeps, fwd, fake_session

    def test_config_default_delay_is_10_seconds(self):
        self.assertEqual(settings.WHATSAPP_FORWARD_DELAY_SECONDS, 10.0)
        self.assertEqual(whatsapp_tasks.FORWARD_DELAY_SECONDS, 10.0)

    def test_multiple_matches_wait_10_seconds_between_forwards(self):
        result, sleeps, fwd, fake_session = self._run_scan(
            scraped_count=3, forward_delay=10.0
        )

        self.assertEqual(result["scraped"], 3)
        self.assertEqual(result["forwarded"], 3)
        self.assertEqual(fwd.await_count, 3)
        # 3 forwards -> exactly 2 pacing pauses, each 10 seconds.
        self.assertEqual(sleeps, [10.0, 10.0])

        for call in fwd.await_args_list:
            _page, target_group, formatted = call.args
            self.assertEqual(target_group, "Forward Group")
            self.assertIn("Job Match Found", formatted)

        # Every forwarded message must be marked forwarded in the DB session.
        raw_rows = fake_session._rows["WhatsAppRawMessage"]
        self.assertEqual(len(raw_rows), 3)
        self.assertTrue(all(msg.forwarded for msg in raw_rows))

    def test_single_match_forwards_immediately_without_pacing_pause(self):
        result, sleeps, fwd, _ = self._run_scan(scraped_count=1, forward_delay=10.0)

        self.assertEqual(result["forwarded"], 1)
        self.assertEqual(fwd.await_count, 1)
        self.assertEqual(sleeps, [])

    def test_delay_is_configurable(self):
        _, sleeps, fwd, _ = self._run_scan(scraped_count=3, forward_delay=5.0)

        self.assertEqual(fwd.await_count, 3)
        self.assertEqual(sleeps, [5.0, 5.0])


if __name__ == "__main__":
    unittest.main()

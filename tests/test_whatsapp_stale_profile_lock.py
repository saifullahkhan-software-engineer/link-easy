"""Stale WhatsApp profile lock / Chromium SingletonLock recovery."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from services.browser_view import BrowserViewManager  # noqa: E402
from services.whatsapp_browser import clear_stale_chromium_singleton  # noqa: E402
from worker.profile_lock import ProfileInUseError  # noqa: E402


class ChromiumSingletonCleanupTests(unittest.TestCase):
    def test_dead_pid_lock_is_removed(self):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / "SingletonLock"
            cookie = Path(root) / "SingletonCookie"
            lock.symlink_to("testhost-99999999")
            cookie.write_text("stale")

            self.assertTrue(clear_stale_chromium_singleton(root))
            self.assertFalse(lock.exists())
            self.assertFalse(cookie.exists())

    def test_live_pid_lock_is_left_alone(self):
        with tempfile.TemporaryDirectory() as root:
            lock = Path(root) / "SingletonLock"
            lock.symlink_to(f"testhost-{os.getpid()}")

            self.assertFalse(clear_stale_chromium_singleton(root))
            self.assertTrue(lock.exists() or lock.is_symlink())

    def test_missing_profile_is_a_noop(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(clear_stale_chromium_singleton(root))


class BrowserViewStaleLockTests(unittest.IsolatedAsyncioTestCase):
    async def test_claim_steals_lock_when_no_local_owner(self):
        manager = BrowserViewManager()
        stolen = object()
        acquire = Mock(side_effect=[ProfileInUseError("busy"), stolen])
        force = Mock(return_value=True)

        with (
            patch("worker.profile_lock.acquire_profile_lock", acquire),
            patch("worker.profile_lock.force_release_profile_lock", force),
            patch(
                "services.whatsapp_browser.clear_stale_chromium_singleton",
                return_value=True,
            ),
        ):
            lock = await manager._claim_whatsapp_profile_lock()

        self.assertIs(lock, stolen)
        self.assertEqual(acquire.call_count, 2)
        force.assert_called_once_with("whatsapp")

    async def test_claim_does_not_steal_from_live_chat(self):
        manager = BrowserViewManager()
        acquire = Mock(side_effect=ProfileInUseError("busy"))
        force = Mock(return_value=True)
        live = SimpleNamespace(_profile_lock=object(), status="running")

        with (
            patch("worker.profile_lock.acquire_profile_lock", acquire),
            patch("worker.profile_lock.force_release_profile_lock", force),
            patch("services.whatsapp_live_browser.live_browser", live),
        ):
            with self.assertRaises(ProfileInUseError):
                await manager._claim_whatsapp_profile_lock()

        force.assert_not_called()
        acquire.assert_called_once()

    async def test_failed_start_releases_the_stored_lock(self):
        manager = BrowserViewManager()
        profile_lock = object()

        class _FailingFactory:
            async def start(self):
                raise RuntimeError("chromium failed")

        with (
            patch.object(
                manager, "_claim_whatsapp_profile_lock", AsyncMock(return_value=profile_lock)
            ),
            patch("worker.profile_lock.release_profile_lock") as release,
            patch(
                "services.browser_view.async_playwright"
                if False
                else "patchright.async_api.async_playwright",
                return_value=_FailingFactory(),
            ),
        ):
            result = await manager.start()

        self.assertEqual(result["status"], "error")
        self.assertIsNone(manager._profile_lock)
        release.assert_called_with(profile_lock)

    async def test_profile_lock_is_acquired_off_the_event_loop(self):
        manager = BrowserViewManager()
        acquiring_thread = {}
        main_thread = __import__("threading").get_ident()

        def slow_acquire(*_args, **_kwargs):
            acquiring_thread["id"] = __import__("threading").get_ident()
            __import__("time").sleep(0.4)
            return object()

        class _FailingFactory:
            async def start(self):
                raise RuntimeError("stop after the lock is taken")

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            for _ in range(8):
                await __import__("asyncio").sleep(0.05)
                ticks += 1

        with (
            patch("worker.profile_lock.acquire_profile_lock", side_effect=slow_acquire),
            patch("worker.profile_lock.release_profile_lock"),
            patch(
                "patchright.async_api.async_playwright",
                return_value=_FailingFactory(),
            ),
        ):
            await __import__("asyncio").gather(manager.start(), heartbeat())

        self.assertNotEqual(
            acquiring_thread.get("id"),
            main_thread,
            "acquire_profile_lock ran on the event loop thread",
        )
        self.assertGreaterEqual(ticks, 6, f"event loop was starved (only {ticks}/8 ticks)")


class ConnectClearsLeftoverBrowsersTests(unittest.IsolatedAsyncioTestCase):
    async def test_connect_stops_error_state_browsers_before_starting(self):
        from api.v1.whatsapp_scanner import connect_whatsapp
        from services.browser_view import browser_view
        from services.whatsapp_live_browser import live_browser

        class _Database:
            def __init__(self):
                self.commits = 0

            async def execute(self, _statement):
                return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

            async def commit(self):
                self.commits += 1

            def add(self, _row):
                return None

            async def refresh(self, row):
                row.id = 1

        db = _Database()
        with (
            patch.object(live_browser, "status", "error"),
            patch.object(live_browser, "stop", AsyncMock()) as stop_live,
            patch.object(browser_view, "status", "error"),
            patch.object(browser_view, "last_error", "stale lock"),
            patch.object(browser_view, "stop", AsyncMock()) as stop_view,
            patch.object(
                browser_view,
                "ensure_started",
                AsyncMock(return_value={"status": "running", "error": None}),
            ),
            patch("api.v1.whatsapp_scanner._spawn_qr_watcher"),
        ):
            response = await connect_whatsapp(
                SimpleNamespace(email="owner@test.dev"), db
            )

        stop_live.assert_awaited()
        stop_view.assert_awaited()
        self.assertEqual(response.status, "waiting_qr")


if __name__ == "__main__":
    unittest.main()

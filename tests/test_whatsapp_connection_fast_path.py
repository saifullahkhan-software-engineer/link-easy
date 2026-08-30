"""Fast-path regressions for WhatsApp QR connection and session capture.

These tests use browser doubles only: no Chromium, Redis, network, or real
WhatsApp account is required.
"""
import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import api.v1.whatsapp_scanner as scanner  # noqa: E402
from services.browser_view import BrowserViewManager  # noqa: E402
from services.whatsapp_browser import (  # noqa: E402
    LOGGED_IN_SELECTOR,
    PANE_SIDE_SELECTOR,
    wait_for_session_capture_ready,
    wait_for_whatsapp_surface,
)


class _VisibleElement:
    async def is_visible(self):
        return True


class _AuthenticatedPage:
    def __init__(self):
        self.wait_calls = []
        self.sidebar = _VisibleElement()

    async def wait_for_selector(self, selector, *, state, timeout):
        self.wait_calls.append((selector, state, timeout))
        return self.sidebar

    async def query_selector(self, selector):
        if selector == PANE_SIDE_SELECTOR:
            return self.sidebar
        return None

    def is_closed(self):
        return False


class SessionCaptureReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_sidebar_is_enough_for_capture(self):
        page = _AuthenticatedPage()

        ready = await wait_for_session_capture_ready(page, timeout_seconds=1)

        self.assertTrue(ready)
        self.assertEqual(page.wait_calls[0][0], LOGGED_IN_SELECTOR)
        self.assertEqual(page.wait_calls[0][1], "visible")

    async def test_connection_surface_does_not_wait_for_conversation_pane(self):
        page = object()
        with (
            patch(
                "services.whatsapp_browser.is_logged_in",
                AsyncMock(return_value=True),
            ),
            patch(
                "services.whatsapp_browser.wait_for_full_whatsapp_surface",
                AsyncMock(return_value=True),
            ) as wait_for_full,
        ):
            surface = await wait_for_whatsapp_surface(
                page,
                timeout_seconds=1,
                require_full_connected_surface=False,
            )

        self.assertEqual(surface, "connected")
        wait_for_full.assert_not_awaited()


class _FakeCdp:
    def __init__(self, calls):
        self.calls = calls

    async def send(self, method, params=None):
        self.calls.append(method)

    def on(self, event, _callback):
        self.calls.append(f"on:{event}")

    async def detach(self):
        self.calls.append("cdp.detach")


class _FakePage:
    def __init__(self, calls):
        self.calls = calls
        self.url = "about:blank"

    async def goto(self, url, **_kwargs):
        self.calls.append("page.goto")
        self.url = url

    async def screenshot(self, **_kwargs):
        self.calls.append("page.screenshot")
        return b"jpeg-frame"


class _FakeContext:
    def __init__(self, page, cdp, calls):
        self.pages = [page]
        self.cdp = cdp
        self.calls = calls

    async def new_page(self):
        raise AssertionError("the existing page should be reused")

    async def new_cdp_session(self, page):
        assert page is self.pages[0]
        self.calls.append("context.new_cdp_session")
        return self.cdp

    async def add_init_script(self, _script):
        self.calls.append("context.add_init_script")

    async def close(self):
        self.calls.append("context.close")


class _FakeChromium:
    def __init__(self, context, calls):
        self.context = context
        self.calls = calls

    async def launch_persistent_context(self, **_kwargs):
        self.calls.append("chromium.launch")
        return self.context


class _FakePlaywright:
    def __init__(self, context, calls):
        self.chromium = _FakeChromium(context, calls)
        self.calls = calls

    async def stop(self):
        self.calls.append("playwright.stop")


class _FakePlaywrightFactory:
    def __init__(self, playwright, calls):
        self.playwright = playwright
        self.calls = calls

    async def start(self):
        self.calls.append("playwright.start")
        return self.playwright


class BrowserFrameStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_screencast_starts_before_whatsapp_navigation(self):
        calls = []
        page = _FakePage(calls)
        cdp = _FakeCdp(calls)
        context = _FakeContext(page, cdp, calls)
        playwright = _FakePlaywright(context, calls)
        manager = BrowserViewManager()
        profile_lock = object()

        with (
            patch.object(
                manager,
                "_claim_whatsapp_profile_lock",
                AsyncMock(return_value=profile_lock),
            ),
            patch(
                "patchright.async_api.async_playwright",
                return_value=_FakePlaywrightFactory(playwright, calls),
            ),
            patch(
                "services.whatsapp_browser.ensure_whatsapp_profile_dir",
                return_value="/tmp/test-whatsapp-profile",
            ),
            patch(
                "services.whatsapp_browser.wait_for_whatsapp_surface",
                AsyncMock(return_value="qr"),
            ) as wait_for_surface,
            patch("worker.profile_lock.release_profile_lock"),
        ):
            result = await manager.start()
            await manager.stop()

        self.assertEqual(result["status"], "running")
        self.assertLess(calls.index("Page.startScreencast"), calls.index("page.goto"))
        self.assertIn("page.screenshot", calls)
        self.assertFalse(
            wait_for_surface.await_args.kwargs["require_full_connected_surface"]
        )


class _WatcherDatabase:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)

    async def commit(self):
        self.commits += 1


class ConnectionWatcherFastPathTests(unittest.IsolatedAsyncioTestCase):
    async def test_sidebar_detection_captures_and_connects_without_settle_waits(self):
        row = SimpleNamespace(
            id=7,
            status="waiting_qr",
            is_active=True,
            storage_state_json=None,
            cookies_json=None,
            updated_at=None,
        )
        db = _WatcherDatabase(row)

        @asynccontextmanager
        async def fake_session():
            yield db

        page = object()
        context = object()
        browser = SimpleNamespace(
            page=page,
            context=context,
            status="running",
            publish_session_event=AsyncMock(),
            stop=AsyncMock(return_value={"status": "idle"}),
        )
        storage_state = {
            "cookies": [{"name": "wa", "value": "linked"}],
            "origins": [],
        }

        import database
        import services.browser_view as browser_module
        import services.whatsapp_browser as whatsapp_module

        with (
            # Per-user rollout: the watcher observes the session's own view.
            patch.object(browser_module, "get_browser_view", return_value=browser),
            patch.object(database, "async_session", fake_session),
            patch.object(
                whatsapp_module,
                "wait_for_session_capture_ready",
                AsyncMock(return_value=True),
            ) as wait_ready,
            patch.object(
                whatsapp_module,
                "get_storage_state",
                AsyncMock(return_value=storage_state),
            ) as get_storage,
            patch.object(
                scanner,
                "_check_2fa_page",
                AsyncMock(return_value=False),
            ) as check_2fa,
        ):
            await scanner._watch_qr_scan(row.id, max_wait_seconds=2)

        self.assertEqual(row.status, "connected")
        self.assertTrue(row.is_active)
        self.assertEqual(row.storage_state_json, storage_state)
        self.assertEqual(row.cookies_json, storage_state["cookies"])
        self.assertEqual(db.commits, 1)
        wait_ready.assert_awaited_once()
        get_storage.assert_awaited_once_with(context)
        check_2fa.assert_not_awaited()
        browser.stop.assert_awaited_once()
        self.assertEqual(
            [call.args[0] for call in browser.publish_session_event.await_args_list],
            ["capturing", "connected"],
        )


if __name__ == "__main__":
    unittest.main()

"""Regression tests for blank-page detection and progressive recovery.

FILE: tests/test_blank_page_recovery.py

Covers automation/actions/utils.py:
  * wait_for_page_render — waits for LinkedIn's SPA to mount instead of
    treating a slow render as a blank page.
  * recover_blank_page — wait-for-render → reload → session health probe
    (feed) → retry, and the session_stale classification the worker uses
    to decide between retrying a lead and suspending the account.
"""
import asyncio
import sys
import types
import unittest

# The production worker installs Patchright.  These unit tests exercise only
# page-state logic, so provide its type-only imports when running in a
# lightweight source checkout without browser dependencies installed.
try:
    import patchright.async_api  # noqa: F401
except ModuleNotFoundError:
    patchright = types.ModuleType("patchright")
    async_api = types.ModuleType("patchright.async_api")
    async_api.Page = type("Page", (), {})
    async_api.Locator = type("Locator", (), {})
    async_api.ElementHandle = type("ElementHandle", (), {})
    patchright.async_api = async_api
    sys.modules["patchright"] = patchright
    sys.modules["patchright.async_api"] = async_api

if "core.logging_config" not in sys.modules:
    logging_config = types.ModuleType("core.logging_config")

    class _NullLogger:
        def debug(self, *_args, **_kwargs):
            pass

        info = debug
        warning = debug
        error = debug

    logging_config.get_logger = lambda _name: _NullLogger()
    logging_config.should_take_screenshots = lambda: False
    logging_config.should_log_debug = lambda: False
    sys.modules["core.logging_config"] = logging_config

from automation.actions import utils as recovery_utils


PROFILE_URL = "https://www.linkedin.com/in/some-lead/"
FEED_URL = recovery_utils.SESSION_HEALTH_URL


class FakeLoop:
    """Event loop stub whose clock advances on every read.

    ``wait_for_page_render`` polls until a deadline taken from
    ``asyncio.get_running_loop().time()``; advancing the clock per read lets
    "stays blank until timeout" scenarios run instantly in tests.
    """

    def __init__(self, step_seconds: float = 0.7):
        self._now = 1000.0
        self._step = step_seconds

    def time(self) -> float:
        self._now += self._step
        return self._now


class FakePage:
    """Minimal Playwright Page stub driven by a navigation script.

    Each script entry describes the page state applied by the next
    ``goto``/``reload``: ``{"blank": bool, "url": optional override}``.
    Between navigations the state is stable, exactly like a real page that
    either rendered or didn't.
    """

    def __init__(self, script):
        self.script = list(script)
        self.url = "about:blank"
        self.blank = True
        self.goto_urls = []
        self.reloads = 0

    def _apply_next_state(self):
        if self.script:
            state = self.script.pop(0)
            self.blank = state.get("blank", False)
            if "url" in state:
                self.url = state["url"]

    async def goto(self, url, **_kwargs):
        self.goto_urls.append(url)
        self.url = url
        self._apply_next_state()

    async def reload(self, **_kwargs):
        self.reloads += 1
        self._apply_next_state()

    # Building blocks used by is_blank_page()
    async def inner_text(self, _selector):
        return "" if self.blank else "LinkedIn profile content " * 20

    async def query_selector(self, _selector):
        return None if self.blank else object()


class RecoveryTestBase(unittest.TestCase):
    def setUp(self):
        self._real_sleep = recovery_utils.asyncio.sleep
        self._real_get_loop = recovery_utils.asyncio.get_running_loop
        # A single shared clock: deadline computations and later reads must
        # observe the same advancing timeline.
        self._fake_loop = FakeLoop()

        async def _no_sleep(_seconds):
            return None

        recovery_utils.asyncio.sleep = _no_sleep
        recovery_utils.asyncio.get_running_loop = lambda: self._fake_loop

    def tearDown(self):
        recovery_utils.asyncio.sleep = self._real_sleep
        recovery_utils.asyncio.get_running_loop = self._real_get_loop

    def run_recover(self, script):
        page = FakePage(script)

        async def _scenario():
            await page.goto(PROFILE_URL)
            return await recovery_utils.recover_blank_page(page, PROFILE_URL)

        recovered, error, session_stale = asyncio.run(_scenario())
        return page, recovered, error, session_stale


class IsBlankPageTests(RecoveryTestBase):
    def test_textless_page_is_blank(self):
        page = FakePage([{"blank": True}])

        async def _check():
            await page.goto(PROFILE_URL)
            return await recovery_utils.is_blank_page(page)

        self.assertTrue(asyncio.run(_check()))

    def test_text_rich_page_without_app_container_is_not_blank(self):
        # Authwall/login/checkpoint pages render real content but have no
        # #app-mount container.  They must be classified by URL, not treated
        # as blank (the old app-container requirement hid the real cause).
        page = FakePage([{"blank": False}])

        async def _no_root(_selector):
            return None

        page.query_selector = _no_root

        async def _check():
            await page.goto(PROFILE_URL)
            return await recovery_utils.is_blank_page(page)

        self.assertFalse(asyncio.run(_check()))


class WaitRenderTests(RecoveryTestBase):
    def test_rendered_page_returns_true_immediately(self):
        page = FakePage([{"blank": False}])

        async def _check():
            await page.goto(PROFILE_URL)
            return await recovery_utils.wait_for_page_render(page)

        self.assertTrue(asyncio.run(_check()))

    def test_page_rendering_mid_poll_is_detected(self):
        page = FakePage([{"blank": True}])

        async def _check():
            await page.goto(PROFILE_URL)

            real_inner_text = page.inner_text
            calls = {"n": 0}

            async def _inner_text(selector):
                calls["n"] += 1
                if calls["n"] >= 3:
                    page.blank = False
                return await real_inner_text(selector)

            page.inner_text = _inner_text
            return await recovery_utils.wait_for_page_render(page)

        self.assertTrue(asyncio.run(_check()))

    def test_blank_page_times_out_false(self):
        page = FakePage([{"blank": True}])

        async def _check():
            await page.goto(PROFILE_URL)
            return await recovery_utils.wait_for_page_render(page, timeout_seconds=1.0)

        self.assertFalse(asyncio.run(_check()))


class RecoverBlankPageTests(RecoveryTestBase):
    def test_slow_render_recovers_without_reload(self):
        # Rendered on first poll — no reload, no session probe.
        page, recovered, error, stale = self.run_recover([{"blank": False}])
        self.assertTrue(recovered)
        self.assertIsNone(error)
        self.assertFalse(stale)
        self.assertEqual(page.reloads, 0)
        self.assertEqual(page.goto_urls, [PROFILE_URL])

    def test_blank_then_reload_recovers(self):
        page, recovered, error, stale = self.run_recover(
            [{"blank": True}, {"blank": False}]
        )
        self.assertTrue(recovered)
        self.assertIsNone(error)
        self.assertFalse(stale)
        self.assertEqual(page.reloads, 1)
        self.assertEqual(page.goto_urls, [PROFILE_URL])

    def test_login_redirect_marks_session_stale(self):
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": False, "url": "https://www.linkedin.com/login?session_expired"},
        ])
        self.assertFalse(recovered)
        self.assertTrue(stale)
        self.assertIn("login", error.lower())
        # Went to the profile, then probed the feed (which redirected).
        self.assertEqual(page.goto_urls, [PROFILE_URL, FEED_URL])

    def test_checkpoint_redirect_marks_session_stale(self):
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": False, "url": "https://www.linkedin.com/checkpoint/challenge/xyz"},
        ])
        self.assertFalse(recovered)
        self.assertTrue(stale)

    def test_healthy_session_retries_navigation_and_recovers(self):
        # Initial + reload blank, feed renders (healthy), retry renders too.
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": False},
            {"blank": False, "url": PROFILE_URL},
        ])
        self.assertTrue(recovered)
        self.assertIsNone(error)
        self.assertFalse(stale)
        self.assertEqual(page.goto_urls, [PROFILE_URL, FEED_URL, PROFILE_URL])

    def test_healthy_session_but_target_still_blank_is_transient(self):
        # Feed renders, but the profile stays blank even on retry — a
        # throttling-style failure that is retryable, NOT a stale session.
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": False},
            {"blank": True, "url": PROFILE_URL},
        ])
        self.assertFalse(recovered)
        self.assertFalse(stale)
        self.assertIn("retried", error.lower())

    def test_rendered_authwall_marks_session_stale_without_reload(self):
        # LinkedIn redirected the profile navigation to the authwall: the
        # page has content, but the session is unusable for authenticated
        # actions such as sending connection requests.
        page, recovered, error, stale = self.run_recover([
            {"blank": False, "url": "https://www.linkedin.com/authwall?trk=bf"},
        ])
        self.assertFalse(recovered)
        self.assertTrue(stale)
        self.assertIn("authwall", error)
        # No reload, no feed probe — the URL told us everything.
        self.assertEqual(page.reloads, 0)
        self.assertEqual(page.goto_urls, [PROFILE_URL])

    def test_retry_landing_on_checkpoint_marks_session_stale(self):
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": False},
            {"blank": False, "url": "https://www.linkedin.com/checkpoint/challenge/abc"},
        ])
        self.assertFalse(recovered)
        self.assertTrue(stale)
        self.assertIn("checkpoint", error)

    def test_feed_also_blank_marks_session_stale(self):
        # Nothing renders anywhere — stop the session (bot detection /
        # restriction / dead session).
        page, recovered, error, stale = self.run_recover([
            {"blank": True},
            {"blank": True},
            {"blank": True},
        ])
        self.assertFalse(recovered)
        self.assertTrue(stale)
        self.assertEqual(page.goto_urls, [PROFILE_URL, FEED_URL])


if __name__ == "__main__":
    unittest.main()

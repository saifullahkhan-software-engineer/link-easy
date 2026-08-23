"""Regression tests for the LinkedIn login-outcome helpers in automation/session.py.

Production logs showed three failure modes these helpers fix:

1. The checkbox step crashed on every run with
   ``'ElementHandle' object has no attribute 'wait_for'`` — ElementHandle
   does not expose ``wait_for``; only Locator does. ``uncheck_all_checkboxes``
   is now Locator-based.
2. A fixed 2-4s sleep after clicking Sign In misclassified a still-in-flight
   navigation (slow proxy) as "still on login page" == "invalid
   credentials". ``wait_for_login_outcome`` polls until the URL leaves the
   login surface or a rejection banner renders.
3. A genuine bounce was reported as a bare 400 with no indication of WHY —
   ``extract_login_error`` now scrapes LinkedIn's on-page rejection banner.
"""
import asyncio
import os
import unittest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from automation.session import (  # noqa: E402
    detect_human_challenge,
    extract_login_error,
    sanitized_url_path,
    uncheck_all_checkboxes,
    wait_for_login_outcome,
)


class FakeSimpleLocator:
    """Minimal stand-in for page.locator(sel).first used by the probes."""

    def __init__(self, visible=False, text=""):
        self.visible = visible
        self.text = text

    async def is_visible(self):
        return self.visible

    async def text_content(self):
        return self.text


class FakeCheckbox:
    """Stands in for ``boxes.nth(i)`` — a real Playwright Locator."""

    def __init__(self, *, visible=True, checked=True, fail_uncheck=False):
        self.visible = visible
        self.checked = checked
        self.fail_uncheck = fail_uncheck
        self.uncheck_calls = 0
        self.click_calls = 0
        self.evaluate_calls = 0

    async def is_visible(self):
        return self.visible

    async def is_checked(self):
        return self.checked

    async def uncheck(self, **kwargs):
        self.uncheck_calls += 1
        if self.fail_uncheck:
            raise RuntimeError("uncheck intercepted")
        self.checked = False

    async def click(self, **kwargs):
        self.click_calls += 1
        self.checked = False

    async def evaluate(self, script):
        self.evaluate_calls += 1
        self.checked = False

    def wait_for(self, *args, **kwargs):
        # The pre-fix implementation reached this path and crashed with
        # "'ElementHandle' object has no attribute 'wait_for'".
        raise AssertionError("wait_for() must not be used by the checkbox helper")


class FakeCheckboxPool:
    def __init__(self, items):
        self.items = items

    async def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]


class FakePage:
    """Routes page.locator(selector) to canned fakes based on the selector."""

    def __init__(self, url="https://www.linkedin.com/login", selector_map=None, boxes=None):
        self.url = url
        self._selector_map = selector_map or {}
        self._boxes = boxes if boxes is not None else FakeCheckboxPool([])
        self.timeouts = 0

    def locator(self, selector):
        if "checkbox" in selector:
            return self._boxes
        fake = self._selector_map.get(selector)
        if fake is None:
            return _FirstOf(FakeSimpleLocator())
        return _FirstOf(fake)

    async def wait_for_timeout(self, ms):
        self.timeouts += 1
        await asyncio.sleep(ms / 1000)  # behave like the real Playwright clock


class _FirstOf:
    """Wraps a single fake so ``page.locator(...).first`` works."""

    def __init__(self, wrapped):
        self.first = wrapped


class SanitizedUrlTests(unittest.TestCase):
    def test_strips_query_and_fragment(self):
        url = "https://www.linkedin.com/checkpoint/challenge/abc?ut=secret-token#frag"
        self.assertEqual(
            sanitized_url_path(url),
            "https://www.linkedin.com/checkpoint/challenge/abc",
        )

    def test_empty_url_is_safe(self):
        self.assertEqual(sanitized_url_path(""), "(unparseable URL)")


class ExtractLoginErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_visible_banner_text(self):
        page = FakePage(selector_map={
            "div[role='alert']": FakeSimpleLocator(
                visible=True, text="Wrong email or password. Try again"
            ),
        })
        result = await extract_login_error(page)
        self.assertEqual(result, "Wrong email or password. Try again")

    async def test_hidden_banner_yields_none(self):
        page = FakePage(selector_map={
            "div[role='alert']": FakeSimpleLocator(visible=False, text="hidden"),
        })
        self.assertIsNone(await extract_login_error(page))

    async def test_no_banner_yields_none(self):
        self.assertIsNone(await extract_login_error(FakePage()))


class DetectHumanChallengeTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_captcha_iframe(self):
        page = FakePage(selector_map={
            "iframe[src*='arkose']": FakeSimpleLocator(visible=True),
        })
        self.assertTrue(await detect_human_challenge(page))

    async def test_no_captcha(self):
        self.assertFalse(await detect_human_challenge(FakePage()))


class WaitForLoginOutcomeTests(unittest.IsolatedAsyncioTestCase):
    async def test_terminal_url_returns_immediately(self):
        page = FakePage(url="https://www.linkedin.com/feed/")
        await wait_for_login_outcome(page, timeout_ms=2000)
        self.assertEqual(page.timeouts, 0)  # no polling sleeps needed

    async def test_login_with_banner_is_definitive(self):
        page = FakePage(
            url="https://www.linkedin.com/login",
            selector_map={
                "div[role='alert']": FakeSimpleLocator(visible=True, text="Wrong password"),
            },
        )
        await wait_for_login_outcome(page, timeout_ms=2000)
        self.assertEqual(page.timeouts, 0)  # banner found on first poll

    async def test_deadline_bounds_slow_navigation(self):
        page = FakePage(url="https://www.linkedin.com/login")
        # No banner, URL never changes: must terminate by deadline, not hang.
        await wait_for_login_outcome(page, timeout_ms=1100)
        self.assertGreaterEqual(page.timeouts, 1)
        self.assertLessEqual(page.timeouts, 6)

    async def test_submit_marker_is_treated_as_in_flight(self):
        page = FakePage(url="https://www.linkedin.com/uas/login-submit")
        await wait_for_login_outcome(page, timeout_ms=1100)
        self.assertGreaterEqual(page.timeouts, 1)


class UncheckAllCheckboxesTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchecks_only_checked_visible_boxes(self):
        checked_visible = FakeCheckbox(visible=True, checked=True)
        already_off = FakeCheckbox(visible=True, checked=False)
        hidden = FakeCheckbox(visible=False, checked=True)
        page = FakePage(boxes=FakeCheckboxPool([checked_visible, already_off, hidden]))

        await uncheck_all_checkboxes(page, context_label="test")

        self.assertFalse(checked_visible.checked)
        self.assertEqual(checked_visible.uncheck_calls, 1)
        self.assertEqual(already_off.uncheck_calls + already_off.click_calls, 0)
        self.assertEqual(hidden.uncheck_calls + hidden.click_calls, 0)

    async def test_falls_back_to_click(self):
        stubborn = FakeCheckbox(visible=True, checked=True, fail_uncheck=True)
        page = FakePage(boxes=FakeCheckboxPool([stubborn]))

        await uncheck_all_checkboxes(page, context_label="test")

        self.assertFalse(stubborn.checked)
        self.assertEqual(stubborn.click_calls, 1)

    async def test_no_boxes_is_noop(self):
        page = FakePage(boxes=FakeCheckboxPool([]))
        await uncheck_all_checkboxes(page, context_label="test")  # must not raise


if __name__ == "__main__":
    unittest.main()

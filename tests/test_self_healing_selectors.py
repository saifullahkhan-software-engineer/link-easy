"""Regression tests for the self-healing selector helpers in automation/session.py.

``find_visible_input_by_type`` / ``find_visible_button_by_text`` used to build
``page.locator(...).filter(has=<ElementHandle>)`` in their final fallback.
``Locator.filter()`` only accepts a Locator, so Playwright crashed with
``'ElementHandle' object has no attribute '_selector'`` — exactly the LinkedIn
login failure seen in production logs (POST /api/v1/linkedin/account -> 400).
These tests pin the fixed nth()-based behaviour.
"""
import os
import unittest
from typing import Optional

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from automation.session import (  # noqa: E402
    find_visible_button_by_text,
    find_visible_input_by_type,
)


class FakeNthLocator:
    """Stands in for ``locator.nth(i)`` — the exact object the helpers return."""

    def __init__(self, *, visible=True, enabled=True, text="", width=200, height=20):
        self.visible = visible
        self.enabled = enabled
        self.text = text
        self.box = {"x": 0, "y": 0, "width": width, "height": height}

    async def bounding_box(self):
        return self.box

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def text_content(self):
        return self.text

    def filter(self, *args, **kwargs):
        # The old implementation called ``.filter(has=<ElementHandle>)``
        # here; any reach of this method is the regression.
        raise AssertionError("filter(has=...) must not be used by the fallbacks")


class FakeBaseLocator:
    """Stands in for ``page.locator(css)``."""

    def __init__(self, items):
        self.items = items
        self.css: Optional[str] = None

    async def count(self):
        return len(self.items)

    def nth(self, i):
        return self.items[i]

    def filter(self, *args, **kwargs):
        raise AssertionError("filter(has=...) must not be used by the fallbacks")


class FakeEmptyRoleLocator:
    async def count(self):
        return 0


class FakePage:
    """Page stub: role/label lookups find nothing; CSS locator hits items."""

    def __init__(self, css_items=None):
        self._css_items = css_items or []
        self.locator_calls: list[str] = []

    def get_by_role(self, *args, **kwargs):
        return FakeEmptyRoleLocator()

    def get_by_label(self, *args, **kwargs):
        return FakeEmptyRoleLocator()

    def get_by_text(self, *args, **kwargs):
        return FakeEmptyRoleLocator()

    def locator(self, css):
        self.locator_calls.append(css)
        base = FakeBaseLocator(self._css_items)
        base.css = css
        return base


class FindVisibleInputTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_visible_enabled_nth_locator(self):
        items = [FakeNthLocator()]
        page = FakePage(css_items=items)

        result = await find_visible_input_by_type(page, "email")

        self.assertIs(result, items[0])

    async def test_skips_hidden_and_disabled_inputs(self):
        items = [
            FakeNthLocator(visible=False),
            FakeNthLocator(enabled=False),
            FakeNthLocator(width=0, height=0),
            FakeNthLocator(),  # good one
        ]
        page = FakePage(css_items=items)

        result = await find_visible_input_by_type(page, "email")

        self.assertIs(result, items[3])

    async def test_email_covers_text_and_untyped_variants(self):
        # LinkedIn A/B-serves the login field as type="email", type="text",
        # or with no type attribute — the fallback CSS must cover all three.
        page = FakePage(css_items=[])
        with self.assertRaises(ValueError):
            await find_visible_input_by_type(page, "email")
        self.assertEqual(len(page.locator_calls), 1)
        css = page.locator_calls[0]
        for fragment in ("input[type='email']", "input[type='text']", "input:not([type])"):
            self.assertIn(fragment, css)
        # Must not accidentally match the password field.
        self.assertNotIn("password", css)

    async def test_no_visible_input_raises_value_error(self):
        with self.assertRaises(ValueError):
            await find_visible_input_by_type(FakePage(css_items=[]), "email")


class FindVisibleButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_last_matching_visible_button(self):
        items = [
            FakeNthLocator(text="Sign in with Google"),
            FakeNthLocator(text="Sign in"),  # main sign-in is the last match
        ]
        page = FakePage(css_items=items)

        result = await find_visible_button_by_text(page, "Sign in")

        self.assertIs(result, items[1])

    async def test_skips_buttons_without_target_text(self):
        items = [FakeNthLocator(text="Join now"), FakeNthLocator(text="Sign in")]
        page = FakePage(css_items=items)

        result = await find_visible_button_by_text(page, "Sign in")

        self.assertIs(result, items[1])

    async def test_no_matching_button_raises_value_error(self):
        with self.assertRaises(ValueError):
            await find_visible_button_by_text(
                FakePage(css_items=[FakeNthLocator(text="Join now")]), "Sign in"
            )


if __name__ == "__main__":
    unittest.main()

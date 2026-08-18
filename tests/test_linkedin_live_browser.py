"""Regression tests for LinkedIn live chat row matching and profile preview."""
import base64
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

from api.v1.linkedin_profile import ProfileScanRequest, scan_profile_pdf  # noqa: E402
from schemas.linkedin_live import LiveMessageItem  # noqa: E402
from schemas.whatsapp_live import LiveMessageItem as WhatsAppLiveMessageItem  # noqa: E402
from services.linkedin_profile_scraper import scrape_profile  # noqa: E402
from services.linkedin_live_browser import (  # noqa: E402
    CHAT_LINK_SELECTOR,
    CHAT_LIST_ROOT_SELECTOR,
    CHAT_NAME_SELECTOR,
    CHAT_PREVIEW_SELECTOR,
    CHAT_ROW_PAGE_SELECTOR,
    CHAT_ROW_SELECTOR,
    CHAT_UNREAD_SELECTOR,
    COMPOSER_SELECTOR,
    LinkedInLiveBrowserManager,
    THREAD_PANEL_SELECTOR,
)


class _Element:
    def __init__(self, text="", attrs=None):
        self.text = text
        self.attrs = attrs or {}
        self.clicked = False

    async def inner_text(self):
        return self.text

    async def get_attribute(self, name):
        return self.attrs.get(name)

    async def click(self):
        self.clicked = True

    async def query_selector(self, _selector):
        return None


class _Row(_Element):
    def __init__(self, href, name, preview="", unread=""):
        super().__init__()
        self.link = _Element(attrs={"href": href})
        self.name = _Element(name)
        self.preview = _Element(preview)
        self.unread = _Element(unread)

    async def query_selector(self, selector):
        if selector == CHAT_LINK_SELECTOR:
            return self.link
        if selector == CHAT_NAME_SELECTOR:
            return self.name
        if selector == CHAT_PREVIEW_SELECTOR:
            return self.preview
        if selector == CHAT_UNREAD_SELECTOR:
            return self.unread
        return None


class _Root:
    def __init__(self, rows):
        self.rows = rows

    async def query_selector_all(self, selector):
        assert selector == CHAT_ROW_SELECTOR
        return self.rows


class _Page:
    def __init__(self, rows):
        self.url = "https://www.linkedin.com/messaging/"
        self.root = _Root(rows)
        self.waited = []
        self.selectors = []

    def is_closed(self):
        return False

    async def query_selector(self, selector):
        self.selectors.append(selector)
        if selector == CHAT_LIST_ROOT_SELECTOR:
            return self.root
        return None

    async def query_selector_all(self, selector):
        self.selectors.append(selector)
        if selector == CHAT_ROW_PAGE_SELECTOR:
            return self.root.rows
        return []

    async def wait_for_selector(self, selector, timeout):
        self.waited.append((selector, timeout))
        return object()


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _SessionContext:
    def __init__(self, account):
        self.account = account

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _query):
        return _Result(self.account)


class _LifecyclePage:
    def __init__(self):
        self.url = "about:blank"

    async def goto(self, url, **_kwargs):
        self.url = url

    async def wait_for_selector(self, _selector, timeout):
        assert timeout == 30000
        return object()


class _Closable:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True

    async def stop(self):
        self.closed = True


class _ExperienceRow:
    def __init__(self, values=None):
        self.values = values or {}

    async def query_selector(self, selector):
        if selector.startswith("h3"):
            value = self.values.get("title")
        elif selector.startswith("p.t-14"):
            value = self.values.get("company")
        elif selector.startswith("h4"):
            value = self.values.get("dates")
        else:
            value = self.values.get("location")
        return _Element(value) if value else None


class _ExperiencePage:
    def __init__(self):
        self.selector = ""

    async def query_selector_all(self, selector):
        self.selector = selector
        return [
            _ExperienceRow(),
            _ExperienceRow(
                {
                    "title": "Analyst",
                    "company": "Acme",
                    "dates": "2024 – Present",
                    "location": "Remote",
                }
            ),
        ]


class _ProfilePage:
    def __init__(self):
        self.url = "https://www.linkedin.com/messaging/thread/original-thread/"
        self.visited = []
        self.section_selectors = []

    async def goto(self, url, **_kwargs):
        self.visited.append(str(url))
        self.url = str(url)

    async def wait_for_selector(self, _selector, timeout):
        assert timeout == 15000
        return object()

    async def evaluate(self, *_args):
        return None

    async def query_selector(self, selector):
        if selector.startswith("main h1"):
            return _Element("Ada Lovelace")
        return None

    async def query_selector_all(self, selector):
        self.section_selectors.append(selector)
        return []


class LinkedInLiveBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_uses_owner_account_and_releases_profile_resources(self):
        from services import linkedin_live_browser as browser_module

        account = SimpleNamespace(id="account-1", owner_email="owner@test.dev")
        page = _LifecyclePage()
        context = _Closable()
        playwright = _Closable()
        profile_lock = object()
        manager = LinkedInLiveBrowserManager()

        with (
            patch.object(
                browser_module,
                "async_session",
                return_value=_SessionContext(account),
            ),
            patch.object(
                browser_module,
                "launch_persistent_browser",
                AsyncMock(return_value=(playwright, None, context, page)),
            ),
            patch(
                "worker.profile_lock.acquire_profile_lock",
                return_value=profile_lock,
            ),
            patch("worker.profile_lock.release_profile_lock") as release,
        ):
            started = await manager.start("owner@test.dev")
            stopped = await manager.stop()

        self.assertEqual(started["status"], "running")
        self.assertEqual(stopped["status"], "idle")
        self.assertTrue(context.closed)
        self.assertTrue(playwright.closed)
        release.assert_called_once_with(profile_lock)

    async def test_list_chats_awaits_dom_text_and_keeps_thread_identity(self):
        row = _Row(
            "https://www.linkedin.com/messaging/thread/2-MTYx%3D/",
            "Ada Lovelace",
            "Thanks for connecting",
            "3 unread",
        )
        manager = LinkedInLiveBrowserManager()
        manager.status = "running"
        manager._page = _Page([row])

        chats = await manager.list_chats(limit=10)

        self.assertEqual(chats[0]["chat_id"], "2-MTYx=")
        self.assertEqual(chats[0]["name"], "Ada Lovelace")
        self.assertEqual(chats[0]["preview"], "Thanks for connecting")
        self.assertEqual(chats[0]["unread_count"], 3)

    async def test_open_chat_matches_rows_without_interpolating_id_into_css(self):
        unsafe_id = '2-thread"with:css[chars]'
        row = _Row(
            "https://www.linkedin.com/messaging/thread/2-thread%22with%3Acss%5Bchars%5D/",
            "Safe row",
        )
        page = _Page([row])
        manager = LinkedInLiveBrowserManager()
        manager.status = "running"
        manager._page = page

        result = await manager.open_chat(unsafe_id)

        self.assertTrue(result["ok"])
        self.assertTrue(row.link.clicked)
        self.assertIn((THREAD_PANEL_SELECTOR, 15000), page.waited)
        self.assertFalse(any(unsafe_id in selector for selector in page.selectors))

    def test_composer_fallbacks_are_scoped_to_main(self):
        for selector in COMPOSER_SELECTOR.split(","):
            self.assertTrue(selector.strip().startswith("main "), selector)

    def test_live_timestamp_models_accept_browser_display_text(self):
        linkedin = LiveMessageItem(
            message_id="event-1",
            text="Hello",
            is_outgoing=False,
            timestamp="Yesterday, 3:42 PM",
        )
        whatsapp = WhatsAppLiveMessageItem(
            whatsapp_message_id="wamid-1",
            text="Hello",
            timestamp="3:42 PM",
        )
        self.assertEqual(linkedin.timestamp, "Yesterday, 3:42 PM")
        self.assertEqual(whatsapp.timestamp, "3:42 PM")

    async def test_profile_experience_ignores_blank_rows_and_stays_section_scoped(self):
        from services.linkedin_profile_scraper import _scrape_experience

        page = _ExperiencePage()
        experience = await _scrape_experience(page)

        self.assertIn("main section:has(#experience)", page.selector)
        self.assertEqual(
            experience,
            [
                {
                    "title": "Analyst",
                    "company": "Acme",
                    "dates": "2024 – Present",
                    "location": "Remote",
                }
            ],
        )

    async def test_profile_scrape_scopes_sections_and_restores_exact_thread_url(self):
        from services import linkedin_profile_scraper as scraper

        page = _ProfilePage()
        original_url = page.url

        @asynccontextmanager
        async def profile_context():
            yield page

        with (
            patch.object(
                scraper.linkedin_live_browser,
                "profile_page",
                return_value=profile_context(),
            ),
            patch.object(scraper.asyncio, "sleep", AsyncMock()),
        ):
            report = await scrape_profile(
                "https://www.linkedin.com/in/ada-lovelace/"
            )

        self.assertEqual(report["basics"]["name"], "Ada Lovelace")
        self.assertEqual(page.visited[-1], original_url)
        self.assertTrue(page.section_selectors)
        self.assertTrue(
            all(
                "main " in selector
                for selector in page.section_selectors
            )
        )

    async def test_profile_scan_returns_report_and_same_generated_pdf(self):
        from api.v1 import linkedin_profile as endpoint

        report = {
            "basics": {"name": "Ada Lovelace"},
            "about": "Pioneer",
            "experience": [],
            "education": [],
            "skills": [],
        }
        fake_pdf = b"%PDF-1.4\npreview"
        manager = endpoint.linkedin_live_browser
        old_status = manager.status
        old_owner = manager._owner_email
        manager.status = "running"
        manager._owner_email = "owner@test.dev"
        try:
            with (
                patch.object(endpoint, "scrape_profile", AsyncMock(return_value=report)),
                patch.object(endpoint, "render_profile_pdf", return_value=fake_pdf),
            ):
                response = await scan_profile_pdf(
                    ProfileScanRequest(
                        profile_url="https://www.linkedin.com/in/ada-lovelace/"
                    ),
                    SimpleNamespace(email="owner@test.dev"),
                )
        finally:
            manager.status = old_status
            manager._owner_email = old_owner

        self.assertEqual(response.report, report)
        self.assertEqual(response.filename, "ada-lovelace-scan.pdf")
        self.assertEqual(base64.b64decode(response.pdf_base64), fake_pdf)


if __name__ == "__main__":
    unittest.main()

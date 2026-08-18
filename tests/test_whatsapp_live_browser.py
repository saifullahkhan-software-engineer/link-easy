"""Regression tests for the WhatsApp live-chat browser manager."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from services.whatsapp_browser import (  # noqa: E402
    CHAT_NAME_SELECTOR,
    CHAT_ROW_SELECTOR,
    MAIN_PANE_SELECTOR,
    MSG_INPUT_SELECTOR,
    PANE_SIDE_SELECTOR,
)
from services.whatsapp_live_browser import (  # noqa: E402
    DEFAULT_CHAT_LIMIT,
    LiveBrowserManager,
)


class _TextElement:
    def __init__(self, text):
        self.text = text

    async def inner_text(self):
        return self.text


class _ChatRow:
    def __init__(self, name):
        self.name = name
        self.clicked = False

    async def query_selector(self, selector):
        if selector == CHAT_NAME_SELECTOR:
            return _TextElement(self.name)
        return None

    async def query_selector_all(self, _selector):
        return []

    async def get_attribute(self, _name):
        return None

    async def click(self):
        self.clicked = True


class _Sidebar:
    def __init__(self, rows):
        self.rows = rows

    async def query_selector_all(self, selector):
        assert selector == CHAT_ROW_SELECTOR
        return self.rows


class _SearchLocator:
    def __init__(self):
        self.value = ""
        self.first = self

    async def fill(self, value, timeout=None):
        self.value = value

    async def click(self, timeout=None):
        return None

    async def press(self, _key):
        self.value = ""


class _Page:
    def __init__(self, rows):
        self.sidebar = _Sidebar(rows)
        self.search = _SearchLocator()
        self.waited_for = []
        self.global_rows_queried = False

    def locator(self, _selector):
        return self.search

    async def wait_for_selector(self, selector, timeout):
        self.waited_for.append((selector, timeout))
        return object()

    async def query_selector(self, selector):
        if selector == PANE_SIDE_SELECTOR:
            return self.sidebar
        if "conversation-header" in selector or selector.startswith("header"):
            return _TextElement("Selected chat")
        return None

    async def query_selector_all(self, selector):
        if selector == CHAT_ROW_SELECTOR:
            self.global_rows_queried = True
            return self.sidebar.rows
        return []


class _FailingPlaywrightFactory:
    async def start(self):
        raise RuntimeError("chromium failed")


class WhatsAppLiveBrowserTests(unittest.IsolatedAsyncioTestCase):
    def test_default_chat_limit_is_top_ten(self):
        self.assertEqual(DEFAULT_CHAT_LIMIT, 10)

    def test_message_composer_selector_cannot_match_sidebar_search(self):
        for selector in MSG_INPUT_SELECTOR.split(","):
            selector = selector.strip()
            if 'role="textbox"' in selector:
                self.assertTrue(
                    selector.startswith("#main ") or selector.startswith("footer "),
                    f"generic textbox selector is not composer-scoped: {selector}",
                )

    async def test_list_chats_returns_top_ten_sidebar_rows_only(self):
        manager = LiveBrowserManager()
        manager.status = "running"
        manager._page = _Page([_ChatRow(f"Chat {i}") for i in range(12)])

        chats = await manager.list_chats()

        self.assertEqual(len(chats), 10)
        self.assertEqual(chats[0]["name"], "Chat 0")
        self.assertEqual(chats[-1]["name"], "Chat 9")
        self.assertFalse(manager._page.global_rows_queried)

    async def test_open_chat_uses_visible_row_and_current_main_selector(self):
        row = _ChatRow("Customer support")
        page = _Page([row])
        manager = LiveBrowserManager()
        manager.status = "running"
        manager._page = page

        result = await manager.open_chat("Customer support")

        self.assertTrue(result["ok"])
        self.assertTrue(row.clicked)
        self.assertIn((MAIN_PANE_SELECTOR, 8000), page.waited_for)
        self.assertEqual(manager.active_chat_name, "Selected chat")

    async def test_read_messages_maps_display_timestamps_and_outgoing_direction(self):
        manager = LiveBrowserManager()
        manager.status = "running"
        manager._page = _Page([])
        manager.active_chat_id = "chat-1"

        raw_messages = [
            {
                "whatsapp_message_id": "newest",
                "sender_name": "Me",
                "message_text": "Latest reply",
                "message_type": "text",
                "is_outgoing": True,
                "timestamp": "3:42 PM",
            },
            {
                "whatsapp_message_id": "oldest",
                "sender_name": "Customer",
                "message_text": "Initial question",
                "message_type": "text",
                "is_outgoing": False,
                "timestamp": "Yesterday, 5:10 PM",
            },
        ]
        with (
            patch(
                "services.whatsapp_live_browser.scrape_messages_from_current_chat",
                AsyncMock(return_value=raw_messages),
            ),
            patch(
                "services.whatsapp_live_browser._scroll_message_history_to_bottom",
                AsyncMock(side_effect=[True, True]),
            ) as restore_bottom,
            patch("services.whatsapp_live_browser.asyncio.sleep", AsyncMock()),
        ):
            messages = await manager.read_messages()

        self.assertEqual(restore_bottom.await_count, 2)
        self.assertEqual(
            [message["whatsapp_message_id"] for message in messages],
            ["oldest", "newest"],
        )
        self.assertFalse(messages[0]["is_outgoing"])
        self.assertTrue(messages[1]["is_outgoing"])
        self.assertEqual(messages[1]["timestamp"], "3:42 PM")

    async def test_read_messages_restores_bottom_when_extraction_fails(self):
        manager = LiveBrowserManager()
        manager.status = "running"
        manager._page = _Page([])
        manager.active_chat_id = "chat-1"

        with (
            patch(
                "services.whatsapp_live_browser.scrape_messages_from_current_chat",
                AsyncMock(side_effect=RuntimeError("DOM replaced")),
            ),
            patch(
                "services.whatsapp_live_browser._scroll_message_history_to_bottom",
                AsyncMock(side_effect=[False, True]),
            ) as restore_bottom,
            patch("services.whatsapp_live_browser.asyncio.sleep", AsyncMock()),
        ):
            with self.assertRaisesRegex(RuntimeError, "DOM replaced"):
                await manager.read_messages()

        self.assertEqual(restore_bottom.await_count, 2)

    async def test_close_chat_clears_server_side_selection(self):
        manager = LiveBrowserManager()
        manager.status = "running"
        manager._page = _Page([])
        manager.active_chat_id = "chat-1"
        manager.active_chat_name = "Chat one"

        result = await manager.close_active_chat()

        self.assertTrue(result["ok"])
        self.assertIsNone(manager.active_chat_id)
        self.assertIsNone(manager.active_chat_name)

    async def test_failed_start_releases_profile_lock_immediately(self):
        manager = LiveBrowserManager()
        profile_lock = object()

        with (
            patch("worker.profile_lock.acquire_profile_lock", return_value=profile_lock),
            patch("worker.profile_lock.release_profile_lock") as release,
            patch(
                "services.whatsapp_live_browser.async_playwright",
                return_value=_FailingPlaywrightFactory(),
            ),
        ):
            result = await manager.start()

        self.assertEqual(result["status"], "error")
        release.assert_called_once_with(profile_lock)
        self.assertIsNone(manager._profile_lock)


if __name__ == "__main__":
    unittest.main()

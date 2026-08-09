"""Regression tests for bounded, incremental WhatsApp message scraping."""
import os
import unittest

# ``whatsapp_browser`` imports application settings even though these tests use
# only its pure scraping routine. Supply harmless values for source checkouts.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from services.whatsapp_browser import (
    MSG_CONTAINER_SELECTOR,
    MSG_IMAGE_SELECTOR,
    MSG_TEXT_SELECTOR,
    scrape_messages_from_current_chat,
)


class _TextElement:
    def __init__(self, value):
        self.value = value

    async def inner_text(self):
        return self.value


class _MessageContainer:
    def __init__(self, message_id: str):
        self.message_id = message_id

    async def get_attribute(self, name):
        if name == "data-id":
            return self.message_id
        return None

    async def query_selector(self, selector):
        if selector == MSG_IMAGE_SELECTOR:
            return None
        if selector == MSG_TEXT_SELECTOR:
            return _TextElement(f"text for {self.message_id}")
        if "msg-sender" in selector:
            return _TextElement("Sender")
        if "msg-time" in selector:
            return _TextElement("10:00")
        return None


class _Page:
    def __init__(self, message_ids):
        self.containers = [_MessageContainer(message_id) for message_id in message_ids]

    async def wait_for_selector(self, selector, timeout):
        assert selector == MSG_CONTAINER_SELECTOR
        assert timeout == 10000

    async def query_selector_all(self, selector):
        assert selector == MSG_CONTAINER_SELECTOR
        return self.containers

    async def query_selector(self, _selector):
        return None

    async def evaluate(self, _expression):
        return None


class WhatsAppIncrementalScanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_scan_returns_only_configured_latest_messages_newest_first(self):
        page = _Page(["m1", "m2", "m3", "m4", "m5"])

        messages = await scrape_messages_from_current_chat(page, message_limit=2)

        self.assertEqual([message["whatsapp_message_id"] for message in messages], ["m5", "m4"])

    async def test_visible_checkpoint_excludes_it_and_every_older_message(self):
        page = _Page(["m1", "m2", "m3", "m4", "m5"])

        messages = await scrape_messages_from_current_chat(
            page,
            last_message_id="m3",
            message_limit=10,
        )

        self.assertEqual([message["whatsapp_message_id"] for message in messages], ["m5", "m4"])

    async def test_latest_checkpoint_produces_no_messages(self):
        page = _Page(["m1", "m2", "m3", "m4", "m5"])

        messages = await scrape_messages_from_current_chat(
            page,
            last_message_id="m5",
            message_limit=10,
        )

        self.assertEqual(messages, [])

    async def test_checkpoint_outside_rendered_window_stays_bounded_to_latest(self):
        page = _Page(["m10", "m11", "m12", "m13", "m14"])

        messages = await scrape_messages_from_current_chat(
            page,
            last_message_id="m2",
            message_limit=2,
        )

        self.assertEqual([message["whatsapp_message_id"] for message in messages], ["m14", "m13"])


if __name__ == "__main__":
    unittest.main()

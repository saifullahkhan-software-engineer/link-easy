"""Regression tests for bounded, incremental WhatsApp message scraping."""
import io
import os
import unittest

from PIL import Image

# ``whatsapp_browser`` imports application settings even though these tests use
# only its pure scraping routine. Supply harmless values for source checkouts.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from services.whatsapp_browser import (
    MSG_CONTAINER_ID_SELECTOR,
    MSG_CONTAINER_SELECTOR,
    MSG_CONTAINER_WRAPPER_SELECTOR,
    MSG_IMAGE_SELECTOR,
    MSG_TEXT_SELECTOR,
    _best_image_payload,
    _image_payload_dimensions,
    _message_id,
    _query_message_containers,
    scrape_messages_from_current_chat,
)


class _TextElement:
    def __init__(self, value):
        self.value = value

    async def inner_text(self):
        return self.value


class _MessageContainer:
    def __init__(self, message_id: str, css_class: str = ""):
        self.message_id = message_id
        self.css_class = css_class

    async def get_attribute(self, name):
        if name == "data-id":
            return self.message_id
        if name == "class":
            return self.css_class
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
        if selector == MSG_CONTAINER_WRAPPER_SELECTOR:
            return self.containers
        if selector == MSG_CONTAINER_ID_SELECTOR:
            return []
        return []

    async def query_selector(self, _selector):
        return None

    async def evaluate(self, _expression):
        return None


class _InnerMessageId:
    def __init__(self, message_id):
        self.message_id = message_id

    async def get_attribute(self, name):
        return self.message_id if name == "data-id" else None


class _GeneratedIdWrapper:
    def __init__(self, generated_id, message_id):
        self.generated_id = generated_id
        self.inner = _InnerMessageId(message_id)

    async def get_attribute(self, name):
        if name == "id":
            return self.generated_id
        return None

    async def query_selector(self, selector):
        if "data-id" in selector:
            return self.inner
        return None


class _AncestorIdWrapper:
    async def get_attribute(self, name):
        return "generated-wrapper-789" if name == "id" else None

    async def evaluate(self, _script):
        return "ancestor-whatsapp-message-012"

    async def query_selector(self, _selector):
        return None


class _MixedContainerPage:
    def __init__(self, wrappers, id_rows):
        self.wrappers = wrappers
        self.id_rows = id_rows

    async def query_selector_all(self, selector):
        if selector == MSG_CONTAINER_WRAPPER_SELECTOR:
            return self.wrappers
        if selector == MSG_CONTAINER_ID_SELECTOR:
            return self.id_rows
        return []


class WhatsAppIncrementalScanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_message_id_prefers_inner_whatsapp_id_over_generated_wrapper_id(self):
        wrapper = _GeneratedIdWrapper("generated-wrapper-123", "whatsapp-message-456")

        self.assertEqual(await _message_id(wrapper), "whatsapp-message-456")

    async def test_message_id_uses_whatsapp_id_from_ancestor_row(self):
        self.assertEqual(
            await _message_id(_AncestorIdWrapper()),
            "ancestor-whatsapp-message-012",
        )

    async def test_nested_wrapper_and_id_rows_are_not_counted_twice(self):
        wrappers = [object(), object(), object()]
        id_rows = [object(), object(), object()]
        page = _MixedContainerPage(wrappers, id_rows)

        containers = await _query_message_containers(page)

        self.assertEqual(containers, wrappers)
        self.assertEqual(len(containers), 3)

    async def test_shared_wrapper_build_uses_individual_id_rows(self):
        wrappers = [object()]
        id_rows = [object(), object(), object()]
        page = _MixedContainerPage(wrappers, id_rows)

        containers = await _query_message_containers(page)

        self.assertEqual(containers, id_rows)

    async def test_rendered_screenshot_replaces_tiny_blob_for_ocr(self):
        tiny_buffer = io.BytesIO()
        Image.new("RGB", (32, 72), "white").save(tiny_buffer, format="PNG")
        rendered_buffer = io.BytesIO()
        Image.new("RGB", (320, 720), "white").save(rendered_buffer, format="PNG")

        selected = _best_image_payload(tiny_buffer.getvalue(), rendered_buffer.getvalue())

        self.assertEqual(_image_payload_dimensions(selected), (320, 720))

    async def test_message_metadata_keeps_visible_timestamp_and_outgoing_direction(self):
        page = _Page([])
        page.containers = [_MessageContainer("sent-message", "message-out")]

        messages = await scrape_messages_from_current_chat(page, message_limit=1)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["timestamp"], "10:00")
        self.assertTrue(messages[0]["is_outgoing"])

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

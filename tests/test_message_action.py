"""Focused regression tests for LinkedIn message-composer selection."""
import sys
import types
import unittest

# The production worker installs Patchright.  These unit tests exercise only
# selector/state logic, so provide its three type-only imports when running in
# a lightweight source checkout without browser dependencies installed.
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

# Do not require the worker's browser/config dependency tree for helper tests.
# The imported helpers do not invoke these functions; lightweight stubs keep
# the tests runnable with the standard-library-only environment used in CI.
if "automation.human" not in sys.modules:
    human = types.ModuleType("automation.human")

    async def _unused_async(*_args, **_kwargs):
        return None

    human.human_click = _unused_async
    human.human_scroll = _unused_async
    human.random_idle_pause = _unused_async
    sys.modules["automation.human"] = human

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
    sys.modules["core.logging_config"] = logging_config

from automation.actions.message import (
    PROFILE_MESSAGE_BUTTON_SELECTORS,
    _compose_has_visible_recipient_picker,
    _find_message_button,
    _is_profile_message_label,
    _pick_enabled_send_button,
    _resolve_compose_box,
)


class FakeButton:
    def __init__(self, *, text="", aria_label="", in_main=True,
                 visible=True, enabled=True):
        self.details = {
            "text": text,
            "ariaLabel": aria_label,
            "inMain": in_main,
        }
        self.visible = visible
        self.enabled = enabled

    async def is_visible(self):
        return self.visible

    async def is_enabled(self):
        return self.enabled

    async def evaluate(self, *_args):
        return self.details


class FakeProfilePage:
    def __init__(self, buttons):
        self.buttons = buttons

    async def query_selector_all(self, selector):
        # The profile action can be returned alongside the persistent global
        # navigation control.  The implementation must reject the latter.
        if selector == PROFILE_MESSAGE_BUTTON_SELECTORS[0]:
            return self.buttons
        return []


class FakeComposeBox:
    def __init__(self, *, recipient_picker=False):
        self.recipient_picker = recipient_picker

    async def is_visible(self):
        return True

    async def bounding_box(self):
        return {"x": 0, "y": 0, "width": 320, "height": 80}

    async def evaluate(self, *_args):
        return self.recipient_picker


class FakeComposePage:
    def __init__(self, boxes):
        self.boxes = boxes

    async def query_selector_all(self, _selector):
        return self.boxes


class FakeSendButton:
    def __init__(self, enabled):
        self.enabled = enabled

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return self.enabled


class FakeSendScope:
    def __init__(self, buttons):
        self.buttons = buttons

    async def query_selector_all(self, _selector):
        return self.buttons


class MessageComposerSelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_message_action_labels_exclude_global_messaging_navigation(self):
        self.assertTrue(_is_profile_message_label("Message", "Message Ada Lovelace"))
        self.assertTrue(_is_profile_message_label("Message", "message"))
        self.assertFalse(_is_profile_message_label("Messaging", "Messaging"))
        self.assertFalse(_is_profile_message_label("New message", "New message"))

    async def test_find_message_button_uses_profile_action_not_header_navigation(self):
        navigation = FakeButton(
            text="Messaging", aria_label="Messaging", in_main=False
        )
        profile_action = FakeButton(
            text="Message", aria_label="Message Ada Lovelace", in_main=True
        )
        page = FakeProfilePage([navigation, profile_action])

        self.assertIs(await _find_message_button(page), profile_action)

    async def test_recipient_picker_marks_blank_compose_box_as_unusable(self):
        blank_compose = FakeComposeBox(recipient_picker=True)
        self.assertTrue(await _compose_has_visible_recipient_picker(blank_compose))

        addressed_compose = FakeComposeBox(recipient_picker=False)
        self.assertFalse(await _compose_has_visible_recipient_picker(addressed_compose))

    async def test_resolve_compose_box_skips_blank_compose_with_recipient_picker(self):
        blank_compose = FakeComposeBox(recipient_picker=True)
        addressed_compose = FakeComposeBox(recipient_picker=False)
        page = FakeComposePage([blank_compose, addressed_compose])

        self.assertIs(await _resolve_compose_box(page, timeout_ms=100), addressed_compose)

    async def test_send_lookup_ignores_stale_disabled_send_button(self):
        disabled_stale_button = FakeSendButton(enabled=False)
        enabled_active_button = FakeSendButton(enabled=True)
        scope = FakeSendScope([disabled_stale_button, enabled_active_button])

        self.assertIs(
            await _pick_enabled_send_button(scope, timeout_ms=100),
            enabled_active_button,
        )


if __name__ == "__main__":
    unittest.main()

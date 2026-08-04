"""Regression tests for structural Connect-button discovery on profile pages."""
import sys
import types
import unittest

# Same lightweight stubs as tests/test_message_action.py: browser and config
# dependencies are not needed for selector/classification logic tests.
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

CLICK_LOG: list = []


def _install_human_stub() -> None:
    human = types.ModuleType("automation.human")

    async def _click(_page, target):
        CLICK_LOG.append(target)

    async def _unused(*_args, **_kwargs):
        return None

    human.human_click = _click
    human.human_type = _unused
    human.human_scroll = _unused
    human.random_idle_pause = _unused
    sys.modules["automation.human"] = human


if "automation.human" not in sys.modules:
    _install_human_stub()

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

from automation.actions import connect as connect_mod
from automation.actions.connect import (
    _MENU_CONNECT_JS,
    _TOP_CARD_JS,
    _classify_top_card_action,
    _open_connect_dialog,
    _poll_top_card_actions,
)

# Discovery polls with real deadlines; compress them so tests stay instant.
connect_mod.CONNECT_SCAN_TIMEOUT_SECONDS = 0.15
connect_mod.MORE_SCAN_TIMEOUT_SECONDS = 0.1
connect_mod.MENU_SCAN_TIMEOUT_SECONDS = 0.2


def info(**overrides):
    """A visible, enabled button record with caller-tweaked fields."""
    base = {
        "text": "",
        "aria": "",
        "control": "",
        "dataview": "",
        "classes": "artdeco-button",
        "haspopup": "",
        "expanded": None,
        "disabled": False,
        "visible": True,
    }
    base.update(overrides)
    return base


class FakeElement:
    def __init__(self, record, name=""):
        self.record = record
        self.name = name

    async def evaluate(self, expression, *_args):
        return self.record

    async def is_visible(self):
        return bool(self.record.get("visible"))

    async def is_enabled(self):
        return not self.record.get("disabled")


class FakeScope:
    """The resolved top card: returns its candidate elements for the scan."""

    def __init__(self, elements):
        self.elements = elements

    async def query_selector_all(self, _selector):
        return self.elements


class FakeHandle:
    def __init__(self, element):
        self._element = element

    def as_element(self):
        return self._element


class FakePage:
    """Page double driving the structural polls.

    ``scope`` models the top card; ``menu_element`` is returned (once) for
    the overflow-menu scan; ``menu_labels`` feeds the menu inventory.
    """

    def __init__(self, scope, *, menu_element=None, menu_labels=None,
                 card_labels=None):
        self.scope = scope
        self.menu_element = menu_element
        self.menu_labels = menu_labels or []
        self.card_labels = card_labels
        self.menu_lookup_attempts = 0

    async def evaluate_handle(self, expression):
        if expression == _TOP_CARD_JS:
            return FakeHandle(self.scope)
        if expression == _MENU_CONNECT_JS:
            self.menu_lookup_attempts += 1
            return FakeHandle(self.menu_element)
        raise AssertionError(f"Unexpected evaluate_handle: {expression[:40]}")

    async def evaluate(self, expression, *_args):
        if "menu" in expression or "role='menu'" in expression:
            return list(self.menu_labels)
        if "document.documentElement.lang" in expression:
            return "en"
        return list(self.card_labels) if self.card_labels else []

    @property
    def keyboard(self):
        class _Keyboard:
            async def press(self, _key):
                return None

        return _Keyboard()

    async def query_selector(self, _selector):
        return None

    async def title(self):
        return "Fake Profile | LinkedIn"

    @property
    def url(self):
        return "https://www.linkedin.com/in/fake/"


class TopCardClassificationTests(unittest.TestCase):
    """Pure classification: Connect / More / ignore for typical LinkedIn labels."""

    def test_connect_variants_are_recognised(self):
        self.assertEqual("connect", _classify_top_card_action(
            info(text="Connect", aria="Invite Jane Doe to connect")))
        self.assertEqual("connect", _classify_top_card_action(
            info(text="Connect")))
        self.assertEqual("connect", _classify_top_card_action(
            info(aria="Invite saif khan to connect")))
        self.assertEqual("connect", _classify_top_card_action(
            info(text="", control="connect")))

    def test_more_trigger_variants_are_recognised(self):
        self.assertEqual("more", _classify_top_card_action(
            info(text="More", aria="More actions")))
        self.assertEqual("more", _classify_top_card_action(
            info(aria="More actions for Jane Doe")))
        self.assertEqual("more", _classify_top_card_action(
            info(dataview="profile-actions-more-actions")))
        # Icon-only artdeco dropdown trigger inside the top card.
        self.assertEqual("more", _classify_top_card_action(
            info(classes="artdeco-dropdown__trigger", aria="More actions")))

    def test_unrelated_and_hazardous_actions_are_rejected(self):
        self.assertIsNone(_classify_top_card_action(info(text="Follow", aria="Follow Jane Doe")))
        self.assertIsNone(_classify_top_card_action(info(text="Message", aria="Message Jane Doe")))
        self.assertIsNone(_classify_top_card_action(info(aria="Disconnect from Jane Doe")))
        self.assertIsNone(_classify_top_card_action(info(text="Remove connection")))
        self.assertIsNone(_classify_top_card_action(info(text="Show more", aria="Show more")))

    def test_invisible_or_disabled_nodes_are_never_actions(self):
        self.assertIsNone(_classify_top_card_action(
            info(text="Connect", visible=False)))
        self.assertIsNone(_classify_top_card_action(
            info(text="Connect", disabled=True)))


class StructuralDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_poll_finds_connect_in_top_card(self):
        connect_el = FakeElement(info(text="Connect", aria="Invite Joe to connect"), "connect")
        scope = FakeScope([
            FakeElement(info(text="Follow", aria="Follow Joe")),
            connect_el,
            FakeElement(info(text="More", aria="More actions")),
        ])
        page = FakePage(scope)

        connect_btn, more_btn, _inventory = await _poll_top_card_actions(page, 0.5)
        self.assertIs(connect_btn, connect_el)
        self.assertIsNotNone(more_btn)

    async def test_poll_reports_more_button_when_connect_is_absent(self):
        more_el = FakeElement(info(text="More", aria="More actions"), "more")
        scope = FakeScope([
            FakeElement(info(text="Follow", aria="Follow Joe")),
            more_el,
        ])
        page = FakePage(scope)

        connect_btn, more_btn, inventory = await _poll_top_card_actions(page, 0.2)
        self.assertIsNone(connect_btn)
        self.assertIs(more_btn, more_el)
        self.assertEqual(2, len(inventory))

    async def test_open_dialog_clicks_top_card_connect_directly(self):
        connect_el = FakeElement(info(text="Connect", aria="Invite Joe to connect"), "connect")
        scope = FakeScope([connect_el])
        page = FakePage(scope)

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertTrue(opened)
        self.assertIsNone(error)
        self.assertFalse(already_connected)
        self.assertIn(connect_el, CLICK_LOG)

    async def test_open_dialog_uses_more_menu_when_connect_is_hidden(self):
        more_el = FakeElement(info(text="More", aria="More actions"), "more")
        menu_connect_el = FakeElement(info(text="Connect", aria="Invite Joe to connect"), "menu-connect")
        scope = FakeScope([
            FakeElement(info(text="Follow", aria="Follow Joe")),
            more_el,
        ])
        page = FakePage(scope, menu_element=menu_connect_el, menu_labels=["Connect | Follow"])

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertTrue(opened)
        self.assertIsNone(error)
        self.assertFalse(already_connected)
        # The More trigger was clicked first, then the Connect menu item.
        self.assertEqual([more_el, menu_connect_el], CLICK_LOG[:2])

    async def test_missing_connect_reports_rendered_inventory(self):
        scope = FakeScope([
            FakeElement(info(text="Follow", aria="Follow Joe")),
            FakeElement(info(text="Message", aria="Message Joe")),
        ])
        page = FakePage(scope, card_labels=["Follow Jane Doe | Follow", "Message Jane Doe | Message"])

        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertFalse(opened)
        self.assertFalse(already_connected)
        self.assertIn("Connect button not found", error)
        self.assertIn("Follow Jane Doe", error)

    async def test_withdraw_item_in_menu_means_already_pending(self):
        more_el = FakeElement(info(text="More", aria="More actions"), "more")
        scope = FakeScope([more_el])
        page = FakePage(
            scope,
            menu_element=None,
            menu_labels=["Withdraw invitation | Move your cursor here"],
        )

        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertFalse(opened)
        self.assertTrue(already_connected)
        self.assertIn("already", error)


if __name__ == "__main__":
    unittest.main()

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
        callback = getattr(target, "on_click", None)
        if callable(callback):
            callback()

    async def _unused(*_args, **_kwargs):
        return None

    human.human_click = _click
    human.human_type = _unused
    human.human_scroll = _unused
    human.random_idle_pause = _unused
    human.human_mouse_move = _unused
    human.find_and_type_resilient = _unused
    human.find_and_click_resilient = _unused
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
    _ACTION_CANDIDATE_SELECTOR,
    _FOLLOWING_STATE_JS,
    _FOLLOW_BUTTON_JS,
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
connect_mod.FOLLOW_CONFIRM_TIMEOUT_SECONDS = 0.05
connect_mod.FOLLOW_SCAN_TIMEOUT_SECONDS = 0.15


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
    def __init__(self, record, name="", on_click=None):
        self.record = record
        self.name = name
        self.on_click = on_click

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

    Follow-first modelling: ``follow_button`` answers the Follow-action scan;
    once it is clicked (``on_click`` sets ``follow_clicked``), top-card scans
    switch to ``post_follow_scope`` and the following-state probe returns
    True — mirroring LinkedIn re-rendering the action row after a follow.
    """

    def __init__(self, scope, *, menu_element=None, menu_labels=None,
                 card_labels=None, follow_button=None, post_follow_scope=None):
        self.scope = scope
        self.menu_element = menu_element
        self.menu_labels = menu_labels or []
        self.card_labels = card_labels
        self.follow_button = follow_button
        self.post_follow_scope = post_follow_scope
        self.follow_clicked = False
        self.menu_lookup_attempts = 0

    async def evaluate_handle(self, expression):
        if expression == _TOP_CARD_JS:
            scope = self.scope
            if self.follow_clicked and self.post_follow_scope is not None:
                scope = self.post_follow_scope
            return FakeHandle(scope)
        if expression == _MENU_CONNECT_JS:
            self.menu_lookup_attempts += 1
            return FakeHandle(self.menu_element)
        if expression == _FOLLOW_BUTTON_JS:
            return FakeHandle(self.follow_button)
        raise AssertionError(f"Unexpected evaluate_handle: {expression[:40]}")

    async def evaluate(self, expression, *_args):
        if expression == _FOLLOWING_STATE_JS:
            return self.follow_clicked
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

    def test_anchor_rendered_connect_is_a_candidate(self):
        # Regression: LinkedIn renders the Connect action on follow-first /
        # creator-mode profiles as an <a> anchor carrying aria-label
        # "Invite <name> to connect" and componentkey="...ConnectButton...connect",
        # with NO role="button". The top-card candidate selector must match such
        # anchors or that Connect is invisible to the scanner (worker logs the
        # misleading "Connect did not appear even after following").
        lowered = _ACTION_CANDIDATE_SELECTOR.lower()
        self.assertIn("a[componentkey*='connectbutton']", lowered)
        self.assertIn("a[aria-label*='connect' i]", lowered)
        self.assertIn("a[aria-label*='invite' i]", lowered)
        # The anchor's label classifies as Connect (same rule as button CTAs).
        self.assertEqual("connect", _classify_top_card_action(
            info(text="Connect", aria="Invite Syed Dawood Shah to connect")))


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


class FollowFirstFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Follow-first profiles: Follow is clicked, then Connect is rescanned."""

    def _make_page(self, pre_scope, post_scope, *, menu_element=None,
                   menu_labels=None, card_labels=None):
        follow_el = FakeElement(info(text="Follow", aria="Follow Joe"), "follow")
        if isinstance(pre_scope, list):
            pre_scope = FakeScope(pre_scope + [follow_el])
        page = FakePage(
            pre_scope,
            menu_element=menu_element,
            menu_labels=menu_labels,
            card_labels=card_labels,
            follow_button=follow_el,
            post_follow_scope=post_scope,
        )
        follow_el.on_click = lambda: setattr(page, "follow_clicked", True)
        return page, follow_el

    async def test_connect_appears_in_top_card_after_following(self):
        connect_el = FakeElement(info(text="Connect", aria="Invite Joe to connect"), "connect")
        post_scope = FakeScope([
            FakeElement(info(text="Following", aria="Unfollow Joe")),
            connect_el,
        ])
        page, follow_el = self._make_page([], post_scope)

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertTrue(opened)
        self.assertIsNone(error)
        self.assertFalse(already_connected)
        # Follow was clicked first, then the newly rendered Connect.
        self.assertEqual([follow_el, connect_el], CLICK_LOG[:2])

    async def test_connect_appears_in_more_menu_after_following(self):
        more_el = FakeElement(info(text="More", aria="More actions"), "more-post")
        menu_connect_el = FakeElement(info(text="Connect", aria="Invite Joe to connect"), "menu-connect")
        post_scope = FakeScope([
            FakeElement(info(text="Following", aria="Unfollow Joe")),
            more_el,
        ])
        page, follow_el = self._make_page([], post_scope, menu_element=menu_connect_el)

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertTrue(opened)
        self.assertIsNone(error)
        self.assertFalse(already_connected)
        self.assertEqual([follow_el, more_el, menu_connect_el], CLICK_LOG[:3])

    async def test_follow_only_profile_reports_precise_error_when_connect_stays_hidden(self):
        post_scope = FakeScope([
            FakeElement(info(text="Following", aria="Unfollow Joe")),
        ])
        page, follow_el = self._make_page(
            [], post_scope,
            card_labels=["Following Joe | Following", "More"],
        )

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertFalse(opened)
        self.assertFalse(already_connected)
        self.assertIn(follow_el, CLICK_LOG)
        self.assertIn("even after following", error)
        self.assertIn("Connect button not found", error)

    async def test_no_follow_button_keeps_original_failure_behaviour(self):
        scope = FakeScope([
            FakeElement(info(text="Message", aria="Message Joe")),
        ])
        page = FakePage(scope, card_labels=["Message Joe | Message"])

        CLICK_LOG.clear()
        opened, error, already_connected = await _open_connect_dialog(page)
        self.assertFalse(opened)
        self.assertFalse(already_connected)
        self.assertEqual([], CLICK_LOG)
        self.assertIn("Connect button not found", error)
        self.assertNotIn("even after following", error)


if __name__ == "__main__":
    unittest.main()

"""
Regression tests for feed-scroll post extraction helpers.

Covers the pure logic added when fixing "feed scrolls but no posts are
captured" (LinkedIn's CSS-modules DOM migration broke the old class-based
selectors):

  * ``_is_post_urn``      — author/entity URNs are never mistaken for posts
  * ``_urn_from_href``    — post permalink href shapes LinkedIn actually serves
  * ``_pseudo_urn``       — deterministic dedup fallback (builtin hash() is
                            randomized per process, which broke cross-run dedup)
  * ``_clean_post_text``  — LinkedIn UI chrome (Like/Comment/Repost/Send,
                            counters) stripped from the scored text
"""
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
if "automation.human" not in sys.modules:
    human = types.ModuleType("automation.human")

    async def _unused_async(*_args, **_kwargs):
        return None

    human.human_scroll = _unused_async
    human.random_idle_pause = _unused_async
    sys.modules["automation.human"] = human

if "automation.actions.utils" not in sys.modules:
    actions_utils = types.ModuleType("automation.actions.utils")
    actions_utils.recover_blank_page = _unused_async
    sys.modules["automation.actions.utils"] = actions_utils

if "core.logging_config" not in sys.modules:
    logging_config = types.ModuleType("core.logging_config")

    class _NullLogger:
        def debug(self, *_args, **_kwargs):
            pass

        info = warning = error = debug

    logging_config.get_logger = lambda _name: _NullLogger()
    logging_config.should_log_debug = lambda: False
    logging_config.should_take_screenshots = lambda: False
    sys.modules["core.logging_config"] = logging_config

from automation.actions.feed_scroll import (
    POST_CONTAINER_SELECTORS,
    _clean_post_text,
    _is_expand_post_text_control,
    _is_post_urn,
    _normalise_post_url,
    _post_identity_key,
    _pseudo_urn,
    _urn_from_href,
)


class UrnClassificationTests(unittest.TestCase):
    def test_accepts_post_urn_kinds(self):
        for urn in (
            "urn:li:activity:7123456789012345678",
            "urn:li:ugcPost:7123456789012345678",
            "urn:li:share:7123456789012345678",
            "urn:li:fsd_update:7123456789012345678",
        ):
            self.assertTrue(_is_post_urn(urn), urn)

    def test_rejects_author_and_entity_urns(self):
        # The actor container exposes data-urn too; using it as the post URN
        # collapses every post from one author into a single "post".
        for urn in (
            "urn:li:person:abc123",
            "urn:li:company:98765",
            "urn:li:organization:123",
            "urn:li:school:456",
            "urn:li:member:789",
        ):
            self.assertFalse(_is_post_urn(urn), urn)

    def test_rejects_garbage(self):
        for value in (None, "", "not a urn", "urn:li:", 123):
            self.assertFalse(_is_post_urn(value))


class UrnFromHrefTests(unittest.TestCase):
    def test_feed_update_permalink(self):
        href = "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/"
        self.assertEqual(
            _urn_from_href(href), "urn:li:activity:7123456789012345678"
        )

    def test_ugc_post_permalink_with_query(self):
        href = "/feed/update/urn:li:ugcPost:7123456789012345678?liTracking=xyz"
        self.assertEqual(
            _urn_from_href(href), "urn:li:ugcPost:7123456789012345678"
        )

    def test_numeric_permalink(self):
        href = "https://www.linkedin.com/feed/update/7123456789012345678/"
        self.assertEqual(
            _urn_from_href(href), "urn:li:activity:7123456789012345678"
        )

    def test_activity_dash_href(self):
        href = "https://www.linkedin.com/posts/john/foo-activity-7123456789012345678-bar/"
        self.assertEqual(
            _urn_from_href(href), "urn:li:activity:7123456789012345678"
        )

    def test_non_post_hrefs(self):
        for href in (None, "", "https://www.linkedin.com/in/john/",
                     "https://www.linkedin.com/posts/john/"):
            self.assertIsNone(_urn_from_href(href))


class PostUrlAndIdentityTests(unittest.TestCase):
    def test_normalises_relative_post_urls(self):
        self.assertEqual(
            _normalise_post_url("/posts/jane_example-activity-7123456789012345678-x?trk=feed"),
            "https://www.linkedin.com/posts/jane_example-activity-7123456789012345678-x",
        )
        self.assertEqual(
            _normalise_post_url("/feed/update/urn:li:activity:7123456789012345678/"),
            "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/",
        )

    def test_url_falls_back_to_urn(self):
        self.assertEqual(
            _normalise_post_url(None, "urn:li:activity:7123456789012345678"),
            "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/",
        )

    def test_identity_dedupes_feed_and_posts_permalink_variants(self):
        self.assertEqual(
            _post_identity_key("urn:li:activity:7123456789012345678"),
            _post_identity_key(None, "/posts/jane_example-activity-7123456789012345678-x"),
        )


class ExpandPostTextControlTests(unittest.TestCase):
    def test_accepts_linkedin_post_expanders(self):
        for text, aria in (
            ("...more", None),
            ("See more", None),
            ("", "Show more post text"),
            ("More", ""),
        ):
            self.assertTrue(_is_expand_post_text_control(text, aria), (text, aria))

    def test_rejects_unrelated_more_controls(self):
        for text, aria in (
            ("More", "More actions"),
            ("See more", "See more comments"),
            ("More comments", None),
            ("Like", None),
        ):
            self.assertFalse(_is_expand_post_text_control(text, aria), (text, aria))


class PseudoUrnTests(unittest.TestCase):
    def test_deterministic(self):
        first = _pseudo_urn("Jane Doe", "Hello world post")
        second = _pseudo_urn("Jane Doe", "Hello world post")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("post_"))

    def test_differs_for_different_content(self):
        a = _pseudo_urn("Jane Doe", "Post one")
        b = _pseudo_urn("Jane Doe", "Post two")
        self.assertNotEqual(a, b)

    def test_null_safe(self):
        self.assertTrue(_pseudo_urn(None, None).startswith("post_"))


class CleanPostTextTests(unittest.TestCase):
    def test_strips_ui_chrome(self):
        raw = (
            "Jane Doe\n"
            "Software Engineer\n"
            "2nd\n"
            "5d\n"
            "Excited to share that we shipped a big feature today!\n"
            "1,234\n"
            "234 comments\n"
            "Like\n"
            "Comment\n"
            "Repost\n"
            "Send\n"
        )
        cleaned = _clean_post_text(raw)
        self.assertIn("shipped a big feature", cleaned)
        for noise in ("Like", "Comment", "Repost", "Send", "1,234",
                      "234 comments"):
            self.assertNotIn(noise, cleaned, noise)
        self.assertNotIn("  ", cleaned)

    def test_keeps_list_item_lines(self):
        cleaned = _clean_post_text("8. Don't give up\nKeep going\n2.3K reactions")
        self.assertIn("8. Don't give up", cleaned)
        self.assertIn("Keep going", cleaned)
        self.assertNotIn("2.3K reactions", cleaned)

    def test_strips_inline_trailing_more_control(self):
        self.assertEqual(
            _clean_post_text("This is a long truncated post …more"),
            "This is a long truncated post",
        )

    def test_empty_and_none(self):
        self.assertEqual(_clean_post_text(None), "")
        self.assertEqual(_clean_post_text("   \n  "), "")


class SelectorSanityTests(unittest.TestCase):
    def test_new_feed_selector_is_primary(self):
        # Regression guard: the stable anchor for the CSS-modules feed page
        # must be the FIRST candidate, before any class-based selector.
        self.assertIn("[data-testid='mainFeed'] [role='listitem']",
                      POST_CONTAINER_SELECTORS)
        self.assertEqual(
            POST_CONTAINER_SELECTORS[0],
            "[data-testid='mainFeed'] [role='listitem']",
        )

    def test_legacy_selectors_kept_as_fallback(self):
        joined = ", ".join(POST_CONTAINER_SELECTORS)
        self.assertIn("div.feed-shared-update-v2", joined)
        self.assertIn("div[data-urn]", joined)


if __name__ == "__main__":
    unittest.main()

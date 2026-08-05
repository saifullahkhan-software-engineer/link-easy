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
    _author_from_anchors,
    _clean_post_text,
    _extract_connection_degree,
    _extract_post_time,
    _find_post_permalink,
    _is_expand_post_text_control,
    _is_post_urn,
    _looks_like_person_name,
    _name_from_aria,
    _normalise_post_url,
    _normalise_profile_url,
    _post_identity_key,
    _pseudo_urn,
    _split_author_name,
    _strip_actor_header,
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

    def test_percent_encoded_feed_permalink(self):
        href = "/feed/update/urn%3Ali%3Aactivity%3A7123456789012345678"
        self.assertEqual(
            _urn_from_href(href), "urn:li:activity:7123456789012345678"
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

    def test_normalises_encoded_and_non_www_feed_permalink(self):
        self.assertEqual(
            _normalise_post_url(
                "https://linkedin.com/feed/update/urn%3Ali%3Aactivity%3A7123456789012345678"
            ),
            "https://www.linkedin.com/feed/update/urn:li:activity:7123456789012345678/",
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
    def test_strips_ui_chrome_and_actor_header(self):
        # Whole-card fallback text (as inner_text renders it): actor header
        # (name, headline, degree, time) first, then the post body, then the
        # reaction chrome.  Only the actual post body must survive.
        raw = (
            "Jane Doe\n"
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
        body = _strip_actor_header(raw, author_name="Jane Doe")
        cleaned = _clean_post_text(body, remove_lines=("Jane Doe",))
        self.assertIn("shipped a big feature", cleaned)
        for noise in ("Jane Doe", "Software Engineer", "2nd", "5d",
                      "Like", "Comment", "Repost", "Send", "1,234",
                      "234 comments"):
            self.assertNotIn(noise, cleaned, noise)
        self.assertNotIn("  ", cleaned)

    def test_strips_degree_and_time_lines_without_header_strip(self):
        cleaned = _clean_post_text("1st\n5d\nActual content here\nFollow")
        self.assertIn("Actual content here", cleaned)
        for noise in ("1st", "5d", "Follow"):
            self.assertNotIn(noise, cleaned, noise)

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

    def test_remove_custom_lines(self):
        cleaned = _clean_post_text("Jane Doe\nBody text\nJane Doe\n", remove_lines=("Jane Doe",))
        self.assertEqual(cleaned, "Body text")

    def test_empty_and_none(self):
        self.assertEqual(_clean_post_text(None), "")
        self.assertEqual(_clean_post_text("   \n  "), "")


class AuthorNameAndProfileTests(unittest.TestCase):
    def test_split_author_name(self):
        self.assertEqual(_split_author_name("Jane Doe"), ("Jane", "Doe"))
        self.assertEqual(_split_author_name("Jean-Luc Picard"), ("Jean-Luc", "Picard"))
        self.assertEqual(_split_author_name("Cher"), ("Cher", None))
        self.assertEqual(_split_author_name("  Alice  Bob  "), ("Alice", "Bob"))
        self.assertEqual(_split_author_name(None), (None, None))
        self.assertEqual(_split_author_name("   "), (None, None))

    def test_normalise_profile_url(self):
        self.assertEqual(
            _normalise_profile_url("/in/janedoe/"),
            "https://www.linkedin.com/in/janedoe/",
        )
        self.assertEqual(
            _normalise_profile_url("//www.linkedin.com/in/janedoe/"),
            "https://www.linkedin.com/in/janedoe/",
        )
        self.assertEqual(
            _normalise_profile_url(
                "https://www.linkedin.com/in/janedoe-123abc?miniProfileUrn=xyz&trk=feed"
            ),
            "https://www.linkedin.com/in/janedoe-123abc",
        )
        self.assertEqual(
            _normalise_profile_url("http://www.linkedin.com/in/jane/"),
            "https://www.linkedin.com/in/jane/",
        )
        for bad in (None, "", "/feed/update/urn:li:activity:123/", "https://example.com/x"):
            self.assertIsNone(_normalise_profile_url(bad), bad)


class ActorMetadataTests(unittest.TestCase):
    def test_extract_connection_degree(self):
        for raw, expected in (
            ("Jane Doe\nSoftware Engineer\n1st\n5d\nBody", "1st"),
            ("Jane Doe\n2nd degree connection\n2d\nBody", "2nd"),
            ("Jane Doe\nFirst degree\n3d\nBody", "1st"),
            ("Just the body text\nwith no degree", None),
        ):
            self.assertEqual(_extract_connection_degree(raw), expected, raw)

    def test_extract_post_time(self):
        for raw, expected in (
            ("Jane Doe\nHeadline\n1st\n5d\nBody", "5d"),
            ("Jane Doe\njust now\nBody", "just now"),
            ("Jane Doe\n2h\nBody", "2h"),
            ("Jane Doe\n1mo\nBody", "1mo"),
            ("Body text without a time token", None),
        ):
            self.assertEqual(_extract_post_time(raw), expected, raw)

    def test_strip_actor_header(self):
        # Header ends with the degree token first; the leftover time line is
        # removed by _clean_post_text in the full pipeline.
        body = _strip_actor_header("Jane Doe\nHeadline\n2nd\n3d\nReal post text", "Jane Doe")
        self.assertEqual(_clean_post_text(body), "Real post text")
        # Header ends with the time token: the whole header is cut at once.
        self.assertEqual(
            _strip_actor_header("Jane Doe\n5d\nReal post text", "Jane Doe"),
            "Real post text",
        )
        # No degree/time token: author-name lines are dropped, body kept.
        self.assertEqual(
            _strip_actor_header("Jane Doe\nReal post text", "Jane Doe"),
            "Real post text",
        )
        self.assertEqual(_strip_actor_header(None), "")


class AnchorBasedExtractionTests(unittest.TestCase):
    """Regression tests for the selector-independent author/permalink logic.

    These reproduce the real LinkedIn CSS-modules DOM captured in the repo
    (``connect_no_button_debug.html``): a repost card whose anchors, in order,
    are the reposter, the original author, and the post permalink — plus
    engagement aggregates that are also ``/in/`` links.  The old class-based
    selectors picked the wrong person (or none) here.
    """

    def _repost_card_anchors(self):
        # Mirror of the captured card: "Syed Dawood Shah reposted this",
        # original post by Saifullah Khan, with an engagement aggregate link.
        return [
            {"text": "", "href": "https://www.linkedin.com/in/syed-dawood-shah-49a14a223/",
             "aria": "View Syed Dawood Shah’s profile"},
            {"text": "Syed Dawood Shah", "href": "/in/syed-dawood-shah-49a14a223/",
             "aria": ""},
            {"text": "", "href": "/in/saifullah-khan-64145b21a/",
             "aria": "View Saifullah Khan’s profile"},
            {"text": "Saifullah Khan", "href": "/in/saifullah-khan-64145b21a/",
             "aria": "Saifullah Khan, Open to work Premium Profile You"},
            {"text": "Muhammad Hammad Bashir and 38 others",
             "href": "/in/muhammad-hammad-bashir/?miniProfileUrn=urn%3Ali%3Afsd_profile...",
             "aria": "Muhammad Hammad Bashir and 38 others reacted to this"},
            {"text": "5d", "href": "/feed/update/urn:li:activity:7454475221819936768/",
             "aria": ""},
        ]

    def test_looks_like_person_name(self):
        for ok in ("Saifullah Khan", "Jean-Luc Picard", "Cher", "Mary Jane Watson"):
            self.assertTrue(_looks_like_person_name(ok), ok)
        for bad in ("", "9 comments", "2 reposts", "Muhammad Bashir and 38 others",
                    "Backend-Focused Software Engineer | Python",
                    "5d", "1st", "Like", "Promoted", "View 3 comments"):
            self.assertFalse(_looks_like_person_name(bad), bad)

    def test_name_from_aria(self):
        self.assertEqual(_name_from_aria("View Saifullah Khan’s profile"),
                         "Saifullah Khan")
        self.assertEqual(
            _name_from_aria("Saifullah Khan, Open to work Premium Profile You"),
            "Saifullah Khan",
        )
        self.assertIsNone(_name_from_aria("9 comments"))
        self.assertIsNone(_name_from_aria(None))

    def test_author_picks_original_author_not_reposter(self):
        # The post is by Saifullah Khan even though the reposter's link appears
        # first — the original author is the LAST plausible-name profile anchor.
        name, url = _author_from_anchors(self._repost_card_anchors())
        self.assertEqual(name, "Saifullah Khan")
        self.assertEqual(url, "https://www.linkedin.com/in/saifullah-khan-64145b21a/")

    def test_author_ignores_engagement_aggregate(self):
        name, url = _author_from_anchors(self._repost_card_anchors())
        self.assertNotEqual(name, "Muhammad Hammad Bashir and 38 others")
        self.assertNotIn("muhammad", (url or ""))

    def test_author_falls_back_to_aria_when_text_empty(self):
        # Avatar-only actor anchor: no visible text, name comes from aria-label.
        anchors = [
            {"text": "", "href": "/in/jane-doe-abc/",
             "aria": "View Jane Doe’s profile"},
        ]
        name, url = _author_from_anchors(anchors)
        self.assertEqual(name, "Jane Doe")
        self.assertEqual(url, "https://www.linkedin.com/in/jane-doe-abc/")

    def test_author_none_when_no_profile_anchor(self):
        self.assertEqual(_author_from_anchors([]), (None, None))
        self.assertEqual(
            _author_from_anchors([{"text": "Like", "href": "#", "aria": ""}]),
            (None, None),
        )

    def test_find_post_permalink_from_anchors(self):
        urn, url = _find_post_permalink(self._repost_card_anchors())
        self.assertEqual(urn, "urn:li:activity:7454475221819936768")
        self.assertEqual(
            url,
            "https://www.linkedin.com/feed/update/urn:li:activity:7454475221819936768/",
        )

    def test_find_post_permalink_handles_posts_shape(self):
        anchors = [
            {"text": "Jane Doe", "href": "/in/jane-doe/", "aria": ""},
            {"text": "2h", "href": "/posts/jane-doe-activity-7123456789012345678-x", "aria": ""},
        ]
        urn, url = _find_post_permalink(anchors)
        self.assertEqual(urn, "urn:li:activity:7123456789012345678")
        self.assertTrue(url.startswith("https://www.linkedin.com/posts/"))

    def test_find_post_permalink_none_when_absent(self):
        self.assertEqual(
            _find_post_permalink([{"text": "Like", "href": "#", "aria": ""}]),
            (None, None),
        )


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

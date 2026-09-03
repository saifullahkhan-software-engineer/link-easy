"""Instagram service: finding the right Facebook Page and explaining an
empty ``/me/accounts``.

Connecting Instagram is a Facebook Login: the Graph API only reaches an
Instagram Business/Creator account through the Facebook Page it is linked
to, so ``get_instagram_account_info`` must find that Page in ``/me/accounts``.
The original implementation

* raised a generic "No Facebook Page found" whenever the list was empty, and
* only ever looked at ``pages[0]`` for a linked Instagram account, so users
  whose Instagram-linked Page was second (or later) in the list were told
  nothing was linked.

These tests pin the diagnosing behaviour:

* ``/me/accounts`` is asked for ``instagram_business_account`` up front and
  every returned Page is checked;
* Pages without any linked Instagram account produce the linking hint;
* an empty list is explained through ``debug_token`` — a token missing
  ``pages_show_list`` gets the permission message, anything else the
  "does not administer any Facebook Page" one (which also spells out that
  this sign-in is separate from the Facebook Page connected elsewhere);
* a failing ``debug_token`` never masks the real problem — it falls back to
  the "no Page" message.

``aiohttp.ClientSession`` is replaced by a fake async-context-manager session
that routes responses by URL and records every request.
"""
import asyncio
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from core.config import settings  # noqa: E402
from services.social import instagram as instagram_module  # noqa: E402
from services.social.instagram import InstagramService  # noqa: E402

GRAPH = InstagramService.GRAPH_API
APP_ID = "ig-app-id"
APP_SECRET = "ig-app-secret"
USER_TOKEN = "user-access-token"


class _FakeResponse:
    """Stands in for the object ``session.get(...)`` yields; only ``json()``
    is used by the service."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """Async-context-manager stand-in for ``aiohttp.ClientSession``.

    ``routes`` maps a URL (or a URL suffix relative to the Graph API root)
    to the JSON payload that request should return. Every call is recorded
    in ``calls`` as ``(method, url, params)`` so tests can assert on the
    exact query the service sent. An unrouted URL is a test bug, not a
    Graph API error, so it raises.
    """

    def __init__(self, routes):
        self.routes = {self._absolute(url): payload for url, payload in routes.items()}
        self.calls = []
        self.closed = False

    @staticmethod
    def _absolute(url):
        return url if url.startswith("http") else f"{GRAPH}/{url.lstrip('/')}"

    def get(self, url, params=None, **_):
        self.calls.append(("GET", url, dict(params or {})))
        if url not in self.routes:
            raise AssertionError(f"unexpected request to {url}; routed: {sorted(self.routes)}")
        return _FakeResponse(self.routes[url])

    def post(self, url, data=None, **_):
        raise AssertionError(f"unexpected POST to {url}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        return False

    # ── assertion helpers ────────────────────────────────────────────────────

    def requests_to(self, url):
        url = self._absolute(url)
        return [call for call in self.calls if call[1] == url]


class InstagramAccountInfoTests(unittest.TestCase):
    def setUp(self):
        self._settings = patch.multiple(
            settings,
            INSTAGRAM_APP_ID=APP_ID,
            INSTAGRAM_APP_SECRET=APP_SECRET,
            INSTAGRAM_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/instagram/callback",
        )
        self._settings.start()
        self.service = InstagramService()

    def tearDown(self):
        self._settings.stop()

    def _run(self, routes):
        """Run ``get_instagram_account_info`` against a fake session serving
        ``routes``; returns ``(result_or_None, exception_or_None, session)``."""
        session = FakeSession(routes)
        with patch.object(instagram_module.aiohttp, "ClientSession", return_value=session):
            try:
                result = asyncio.run(self.service.get_instagram_account_info(USER_TOKEN))
            except Exception as exc:  # the service raises plain Exception
                return None, exc, session
        return result, None, session

    # ── Happy path ───────────────────────────────────────────────────────────

    def test_returns_the_instagram_account_linked_to_the_page(self):
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-1", "name": "My Shop", "instagram_business_account": {"id": "ig-1"}},
                ]
            },
            "ig-1": {"id": "ig-1", "username": "myshop"},
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(exc)
        self.assertEqual(info["account_id"], "ig-1")
        self.assertEqual(info["account_name"], "myshop")
        self.assertEqual(info["extra_data"], {"page_id": "page-1"})
        # ``get_account_info`` is the uniform-interface alias the callback uses.
        self.assertIs(InstagramService.get_account_info, InstagramService.get_instagram_account_info)

        # The Page list must be asked for the Instagram link up front — that
        # is what lets every Page be checked without a request per Page.
        (accounts_call,) = session.requests_to("me/accounts")
        requested_fields = set(accounts_call[2]["fields"].split(","))
        self.assertIn("instagram_business_account", requested_fields)
        self.assertIn("id", requested_fields)
        self.assertEqual(accounts_call[2]["access_token"], USER_TOKEN)
        # Nothing to diagnose on the happy path.
        self.assertEqual(session.requests_to("debug_token"), [])
        self.assertTrue(session.closed)

    # ── Multi-page walk ──────────────────────────────────────────────────────

    def test_walks_all_pages_and_uses_the_one_with_an_instagram_link(self):
        # The Instagram-linked Page is NOT pages[0]; the old code only ever
        # checked the first Page and reported "no Instagram account linked".
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-personal", "name": "Personal Blog"},
                    {"id": "page-brand", "name": "Brand", "instagram_business_account": {"id": "ig-brand"}},
                    {"id": "page-other", "name": "Other", "instagram_business_account": {"id": "ig-other"}},
                ]
            },
            "ig-brand": {"id": "ig-brand", "username": "brand.official"},
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(exc)
        self.assertEqual(info["account_id"], "ig-brand")
        self.assertEqual(info["account_name"], "brand.official")
        self.assertEqual(info["extra_data"], {"page_id": "page-brand"})
        # Only the chosen account's username is fetched — no per-Page probing.
        self.assertEqual(len(session.requests_to("ig-brand")), 1)
        self.assertEqual(session.requests_to("ig-other"), [])
        self.assertEqual(session.requests_to("page-personal"), [])

    # ── Pages exist, none linked to Instagram ────────────────────────────────

    def test_pages_without_any_linked_instagram_account_get_the_linking_hint(self):
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-1", "name": "First"},
                    {"id": "page-2", "name": "Second"},
                ]
            },
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(info)
        self.assertIsNotNone(exc)
        message = str(exc)
        self.assertEqual(message, instagram_module.NO_LINKED_INSTAGRAM_ACCOUNT)
        self.assertIn("administers Facebook Pages", message)
        self.assertIn("none of them has an Instagram Business/Creator account linked", message)
        self.assertIn("Accounts Center", message)
        self.assertIn("Linked accounts", message)
        self.assertIn("then reconnect", message)
        # Having Pages is not an empty-list situation: no token diagnosis.
        self.assertEqual(session.requests_to("debug_token"), [])

    # ── Empty list: diagnose through debug_token ─────────────────────────────

    def test_empty_list_without_pages_show_list_blames_the_missing_permission(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "data": {
                    "app_id": APP_ID,
                    "is_valid": True,
                    "scopes": ["instagram_basic", "instagram_content_publish", "public_profile"],
                }
            },
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(info)
        message = str(exc)
        self.assertEqual(message, instagram_module.MISSING_PAGES_PERMISSION)
        self.assertIn("pages_show_list", message)
        self.assertIn("See a list of your Pages", message)
        self.assertIn("reconnect", message)

        # debug_token must inspect the user's token, authenticated with the
        # app access token (app_id|app_secret).
        (debug_call,) = session.requests_to("debug_token")
        self.assertEqual(debug_call[2]["input_token"], USER_TOKEN)
        self.assertEqual(debug_call[2]["access_token"], f"{APP_ID}|{APP_SECRET}")

    def test_empty_list_with_pages_show_list_means_no_page_is_administered(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "data": {
                    "app_id": APP_ID,
                    "is_valid": True,
                    "scopes": [
                        "instagram_basic",
                        "instagram_content_publish",
                        "pages_show_list",
                        "pages_read_engagement",
                        "public_profile",
                    ],
                }
            },
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(info)
        message = str(exc)
        self.assertEqual(message, instagram_module.NO_FACEBOOK_PAGE)
        self.assertIn("does not administer any Facebook Page", message)
        # The user may have a Facebook Page connected on the Facebook card
        # and assume Instagram sees it; it does not — different sign-in.
        self.assertIn("separate from the Facebook Page", message)
        self.assertNotIn("pages_show_list", message)
        self.assertEqual(len(session.requests_to("debug_token")), 1)

    def test_empty_list_falls_back_to_no_page_message_when_debug_token_fails(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "error": {
                    "message": "(#100) You must provide an app access token",
                    "type": "OAuthException",
                    "code": 100,
                }
            },
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(info)
        message = str(exc)
        self.assertEqual(message, instagram_module.NO_FACEBOOK_PAGE)
        self.assertIn("does not administer any Facebook Page", message)
        # The debug_token failure must not leak over the actionable message.
        self.assertNotIn("app access token", message)
        self.assertNotIn("debug_token", message)
        self.assertEqual(len(session.requests_to("debug_token")), 1)

    def test_empty_list_falls_back_when_debug_token_has_no_scopes(self):
        # A malformed/partial debug_token payload is treated like a failure.
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {"data": {"app_id": APP_ID, "is_valid": False}},
        }

        info, exc, _session = self._run(routes)

        self.assertIsNone(info)
        self.assertEqual(str(exc), instagram_module.NO_FACEBOOK_PAGE)

    # ── Graph API errors still surface as before ─────────────────────────────

    def test_graph_error_on_me_accounts_is_reported_not_diagnosed(self):
        routes = {
            "me/accounts": {
                "error": {"message": "Error validating access token", "type": "OAuthException", "code": 190}
            },
        }

        info, exc, session = self._run(routes)

        self.assertIsNone(info)
        self.assertIn("Failed to get Facebook pages", str(exc))
        self.assertIn("Error validating access token", str(exc))
        self.assertIn("(code 190)", str(exc))
        self.assertEqual(session.requests_to("debug_token"), [])

    # ── User-facing messages survive the callback redirect ───────────────────

    def test_diagnostic_messages_fit_the_callback_redirect_limit(self):
        # api/v1/social_scheduler._frontend_redirect keeps error[:300]; the
        # actionable part of each message must not be truncated away.
        for message in (
            instagram_module.NO_LINKED_INSTAGRAM_ACCOUNT,
            instagram_module.MISSING_PAGES_PERMISSION,
            instagram_module.NO_FACEBOOK_PAGE,
        ):
            with self.subTest(message=message[:40]):
                self.assertLessEqual(len(message), 300)


if __name__ == "__main__":
    unittest.main()

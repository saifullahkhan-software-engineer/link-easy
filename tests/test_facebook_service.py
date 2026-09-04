"""Facebook Page service: picking the right Page and explaining an empty
``/me/accounts``.

Connecting a Facebook Page is a Facebook Login followed by ``/me/accounts``;
the chosen Page's own access token is what gets stored and used to upload
video. The original ``exchange_code``

* raised a generic "No Facebook Page was found for this account" whenever
  the list was empty — indistinguishable from a sign-in where the user
  unticked *"See a list of your Pages"* (``pages_show_list``), which returns
  an empty list with no error; and
* only ever looked at ``pages[0]`` — and gave up entirely if that Page had
  no ``access_token``, even when a usable Page was next in the list.

These tests pin the diagnosing behaviour, mirroring
``tests/test_instagram_service.py`` for the Instagram flow:

* every Page is considered; a Page the user can create content on is
  preferred, otherwise the first Page with a token;
* Pages without any token get an explicit message instead of "not found";
* an empty list is explained through ``debug_token`` — missing
  ``pages_show_list`` → the permission message, otherwise (or when
  ``debug_token`` fails) → "does not administer any Facebook Page",
  naming the account that actually signed in (from ``/me``) when the name
  can be determined — the usual cause of an empty list in a fresh browser
  is a sign-in to the *wrong* Facebook account;
* a failing ``/me`` name lookup keeps the generic message (never masks the
  diagnosis, never prints "(None)").

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
from services.social import facebook as facebook_module  # noqa: E402
from services.social.facebook import FacebookService  # noqa: E402

GRAPH = FacebookService.GRAPH_API
APP_ID = "fb-app-id"
APP_SECRET = "fb-app-secret"
USER_TOKEN = "user-access-token"
TOKEN_EXCHANGE = {"access_token": USER_TOKEN, "token_type": "bearer", "expires_in": 5183944}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """Async-context-manager stand-in for ``aiohttp.ClientSession`` that
    routes by URL (absolute, or relative to the Graph API root) and records
    every request as ``(method, url, params)``."""

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

    def requests_to(self, url):
        url = self._absolute(url)
        return [call for call in self.calls if call[1] == url]


class GraphApiVersionTests(unittest.TestCase):
    """The Graph API version is centralized in ``meta_graph`` and shared by
    both Meta services — Meta sunsets versions on a ~2-year clock (v18.0
    died 2026-01-26, breaking the Instagram flow; v20.0 dies 2026-09-24),
    and per-file hardcoded versions are exactly how the dead v18.0 slipped
    in. These tests fail if a service drifts back to its own hardcoded
    version or the central version goes stale.

    When the supported-version test fails, bump ``GRAPH_API_VERSION`` in
    ``services/social/meta_graph.py`` (check the live window at
    developers.facebook.com/docs/graph-api/changelog) — one line fixes both
    platforms.
    """

    def test_both_services_share_the_central_graph_version(self):
        from services.social import instagram, meta_graph

        self.assertEqual(FacebookService.GRAPH_API, meta_graph.GRAPH_API_BASE)
        self.assertEqual(instagram.InstagramService.GRAPH_API, meta_graph.GRAPH_API_BASE)
        # The base URL and the OAuth dialog must carry the declared version.
        self.assertTrue(meta_graph.GRAPH_API_BASE.endswith(f"/{meta_graph.GRAPH_API_VERSION}"))
        self.assertIn(meta_graph.GRAPH_API_VERSION, meta_graph.OAUTH_DIALOG)

    def test_graph_version_is_still_supported(self):
        from services.social import meta_graph

        # Versions supported as of September 2026 per Meta's changelog.
        supported = {"v21.0", "v22.0", "v23.0", "v24.0", "v25.0", "v26.0"}
        self.assertIn(
            meta_graph.GRAPH_API_VERSION,
            supported,
            f"{meta_graph.GRAPH_API_VERSION} is no longer supported — "
            "bump GRAPH_API_VERSION in services/social/meta_graph.py",
        )


class FacebookExchangeCodeTests(unittest.TestCase):
    def setUp(self):
        self._settings = patch.multiple(
            settings,
            FACEBOOK_APP_ID=APP_ID,
            FACEBOOK_APP_SECRET=APP_SECRET,
            FACEBOOK_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/facebook/callback",
        )
        self._settings.start()
        self.service = FacebookService()

    def tearDown(self):
        self._settings.stop()

    def _run(self, routes):
        """Run ``exchange_code`` against a fake session; returns
        ``(tokens_or_None, exception_or_None, session)``."""
        session = FakeSession({"oauth/access_token": TOKEN_EXCHANGE, **routes})
        with patch.object(facebook_module.aiohttp, "ClientSession", return_value=session):
            try:
                result = asyncio.run(self.service.exchange_code("auth-code"))
            except ValueError as exc:
                return None, exc, session
        return result, None, session

    # ── Happy path ───────────────────────────────────────────────────────────

    def test_returns_the_page_token_of_the_only_page(self):
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-1", "name": "My Shop", "access_token": "page-token-1", "tasks": ["CREATE_CONTENT"]},
                ]
            },
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(exc)
        # The *Page* token is stored — never the user token.
        self.assertEqual(tokens, {"access_token": "page-token-1", "refresh_token": None, "expires_in": 5183944})
        (accounts_call,) = session.requests_to("me/accounts")
        self.assertEqual(accounts_call[2]["access_token"], USER_TOKEN)
        fields = set(accounts_call[2]["fields"].split(","))
        self.assertTrue({"id", "name", "access_token", "tasks"} <= fields)
        self.assertEqual(session.requests_to("debug_token"), [])
        self.assertTrue(session.closed)

    # ── Multi-page selection ─────────────────────────────────────────────────

    def test_skips_a_tokenless_first_page_and_uses_the_next_usable_one(self):
        # Old code: pages[0] had no access_token → "No Facebook Page was
        # found", even though a perfectly usable Page was right behind it.
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-no-token", "name": "Read-only Page"},
                    {"id": "page-2", "name": "Brand", "access_token": "page-token-2", "tasks": ["CREATE_CONTENT"]},
                ]
            },
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(exc)
        self.assertEqual(tokens["access_token"], "page-token-2")

    def test_prefers_a_page_the_user_can_create_content_on(self):
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-analyst", "name": "Analytics only", "access_token": "tok-analyst", "tasks": ["ANALYZE"]},
                    {
                        "id": "page-editor",
                        "name": "Editor here",
                        "access_token": "tok-editor",
                        "tasks": ["PROFILE_PLUS_CREATE_CONTENT", "PROFILE_PLUS_ANALYZE"],
                    },
                ]
            },
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(exc)
        self.assertEqual(tokens["access_token"], "tok-editor")

    def test_falls_back_to_the_first_page_with_a_token_when_tasks_are_unknown(self):
        # No Page advertises a content task (older API shapes omit ``tasks``)
        # — keep the previous behaviour of taking the first Page with a token.
        routes = {
            "me/accounts": {
                "data": [
                    {"id": "page-a", "name": "A", "access_token": "tok-a"},
                    {"id": "page-b", "name": "B", "access_token": "tok-b"},
                ]
            },
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(exc)
        self.assertEqual(tokens["access_token"], "tok-a")

    # ── Pages listed, none usable ────────────────────────────────────────────

    def test_pages_without_any_token_get_an_explicit_message(self):
        routes = {
            "me/accounts": {"data": [{"id": "page-1", "name": "First"}, {"id": "page-2", "name": "Second"}]},
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), facebook_module.NO_PAGE_TOKEN)
        self.assertIn("no Page access token", str(exc))
        self.assertIn("reconnect", str(exc))
        # Having Pages is not an empty-list situation: no token diagnosis.
        self.assertEqual(session.requests_to("debug_token"), [])

    # ── Empty list: diagnose through debug_token ─────────────────────────────

    def test_empty_list_without_pages_show_list_blames_the_missing_permission(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "data": {"app_id": APP_ID, "is_valid": True, "scopes": ["pages_manage_posts", "public_profile"]}
            },
            "me": {"id": "fb-user-1", "name": "Jane Doe"},
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(tokens)
        message = str(exc)
        self.assertEqual(message, facebook_module.MISSING_PAGES_PERMISSION)
        self.assertIn("pages_show_list", message)
        self.assertIn("See a list of your Pages", message)
        self.assertIn("Disconnect Facebook and reconnect", message)

        # The user's token is inspected, authenticated with app_id|app_secret.
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
                    "scopes": ["pages_show_list", "pages_read_engagement", "pages_manage_posts", "publish_video"],
                }
            },
            "me": {"id": "fb-user-1", "name": "Jane Doe"},
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(tokens)
        message = str(exc)
        # The account that actually completed the sign-in is named, so a
        # sign-in to the wrong account in a fresh browser is visible.
        self.assertEqual(message, facebook_module.no_page_message("Jane Doe"))
        self.assertIn("(Jane Doe)", message)
        self.assertIn("does not administer any Facebook Page", message)
        self.assertIn("only follow or like does not count", message)
        self.assertNotIn("pages_show_list", message)
        self.assertEqual(len(session.requests_to("debug_token")), 1)

    def test_empty_list_names_the_signed_in_account_when_me_has_no_name(self):
        # A Graph ``/me`` that returns no usable name falls back to the
        # generic wording rather than "(None)".
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "data": {
                    "app_id": APP_ID,
                    "is_valid": True,
                    "scopes": ["pages_show_list"],
                }
            },
            "me": {"id": "fb-user-1"},
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), facebook_module.NO_FACEBOOK_PAGE)
        self.assertNotIn("(None)", str(exc))

    def test_empty_list_names_the_account_even_when_debug_token_fails(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "error": {"message": "(#100) You must provide an app access token", "type": "OAuthException", "code": 100}
            },
            "me": {"id": "fb-user-1", "name": "Jane Doe"},
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), facebook_module.no_page_message("Jane Doe"))

    def test_empty_list_falls_back_to_no_page_message_when_debug_token_fails(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "error": {"message": "(#100) You must provide an app access token", "type": "OAuthException", "code": 100}
            },
            "me": {"id": "fb-user-1", "name": "Jane Doe"},
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(tokens)
        message = str(exc)
        self.assertEqual(message, facebook_module.no_page_message("Jane Doe"))
        self.assertNotIn("app access token", message)
        self.assertNotIn("debug_token", message)
        self.assertEqual(len(session.requests_to("debug_token")), 1)

    def test_empty_list_falls_back_when_debug_token_has_no_scopes(self):
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {"data": {"app_id": APP_ID, "is_valid": False}},
            "me": {"id": "fb-user-1", "name": "Jane Doe"},
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), facebook_module.no_page_message("Jane Doe"))

    def test_empty_list_stays_generic_when_the_name_lookup_fails(self):
        # A failing ``/me`` must not mask the diagnosis: generic message.
        routes = {
            "me/accounts": {"data": []},
            "debug_token": {
                "data": {"app_id": APP_ID, "is_valid": True, "scopes": ["pages_show_list"]}
            },
            "me": {"error": {"message": "Error validating access token", "type": "OAuthException", "code": 190}},
        }

        tokens, exc, _session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), facebook_module.NO_FACEBOOK_PAGE)
        self.assertNotIn("(None)", str(exc))
        self.assertNotIn("Error validating", str(exc))

    # ── Graph API errors still surface as before ─────────────────────────────

    def test_graph_error_on_me_accounts_is_reported_not_diagnosed(self):
        routes = {
            "me/accounts": {"error": {"message": "Error validating access token", "type": "OAuthException", "code": 190}},
        }

        tokens, exc, session = self._run(routes)

        self.assertIsNone(tokens)
        self.assertEqual(str(exc), "Error validating access token")
        self.assertEqual(session.requests_to("debug_token"), [])

    def test_oauth_error_is_reported_before_pages_are_read(self):
        session = FakeSession({"oauth/access_token": {"error": {"message": "Invalid verification code format."}}})
        with patch.object(facebook_module.aiohttp, "ClientSession", return_value=session):
            with self.assertRaisesRegex(ValueError, "Invalid verification code"):
                asyncio.run(self.service.exchange_code("bad-code"))
        self.assertEqual(session.requests_to("me/accounts"), [])

    # ── User-facing messages survive the callback redirect ───────────────────

    def test_diagnostic_messages_fit_the_callback_redirect_limit(self):
        # api/v1/social_scheduler._frontend_redirect keeps error[:300].
        for message in (
            facebook_module.MISSING_PAGES_PERMISSION,
            facebook_module.NO_FACEBOOK_PAGE,
            facebook_module.NO_PAGE_TOKEN,
            facebook_module.no_page_message("A Rather Long Person Name Here"),  # long name
        ):
            with self.subTest(message=message[:40]):
                self.assertLessEqual(len(message), 300)

    def test_no_page_message_truncates_very_long_names(self):
        message = facebook_module.no_page_message("A" * 200)
        self.assertLessEqual(len(message), 300)
        self.assertNotIn("A" * 29, message)


if __name__ == "__main__":
    unittest.main()

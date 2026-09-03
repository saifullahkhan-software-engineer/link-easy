"""YouTube service: OAuth scopes and account-info enrichment.

The OAuth client's scope set changed from

    youtube.readonly + youtube.upload

to

    youtube.readonly + youtube.upload + userinfo.profile

These tests pin the service-side half of that change:

* ``SCOPES`` must request all three, so the authorization URL (and the
  consent screen the user sees) actually asks for ``userinfo.profile``.
* ``get_channel_info`` uses the new scope to store which Google account
  (name/email/picture) is connected — best-effort only. Connections made
  before the scope change hold tokens without it (and the People API may
  not even be enabled on the project), so a failing profile fetch must
  never break the connect; the channel info alone is still returned.
* The ``_client`` refactor (service name/version per API) keeps the
  upload path pointing at ``youtube`` ``v3``.
"""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from core.config import settings  # noqa: E402
from services.social.pkce import generate_code_verifier  # noqa: E402
from services.social.youtube import YouTubeService  # noqa: E402

USERINFO_SCOPE = "https://www.googleapis.com/auth/userinfo.profile"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class FakeAPIClient:
    """Stands in for googleapiclient discovery clients for youtube/v3 and
    people/v1. Records which service/version each client was built for."""

    def __init__(self, channel=None, profile=None, profile_error=None):
        self.channel = channel
        self.profile = profile
        self.profile_error = profile_error
        self.calls = []
        self.last_upload_body = None

    def channels(self):
        outer = self

        class _Channels:
            def list(self, part=None, mine=None):
                outer.calls.append(("youtube.channels.list", part, mine))
                return _Request(outer.channel or {"items": []})

        return _Channels()

    def videos(self):
        outer = self

        class _Videos:
            def insert(self, part=None, body=None, media_body=None):
                outer.calls.append(("youtube.videos.insert", part))
                outer.last_upload_body = body
                return _Request({"id": "vid-123"})

        return _Videos()

    def people(self):
        outer = self

        class _People:
            def get(self, name, params=None):
                outer.calls.append(("people.people.get", name, params))
                if outer.profile_error is not None:
                    raise outer.profile_error
                return _Request(outer.profile or {})

        return _People()


class YouTubeServiceTests(unittest.TestCase):
    def setUp(self):
        self._settings = patch.multiple(
            settings,
            YOUTUBE_CLIENT_ID="yt-id",
            YOUTUBE_CLIENT_SECRET="yt-secret",
            YOUTUBE_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback",
        )
        self._settings.start()
        self.client = FakeAPIClient()
        self._built = []
        # patch.object replaces a class-level function, so the lambda's first
        # param is the service instance; the test case is reached by closure.
        self._client_patcher = patch.object(
            YouTubeService,
            "_client",
            lambda service, name, version, access_token, refresh_token: (
                self._built.append((name, version)),
                self.client,
            )[-1],
        )
        self._client_patcher.start()
        self.service = YouTubeService()

    def tearDown(self):
        self._client_patcher.stop()
        self._settings.stop()

    # ── Scopes ───────────────────────────────────────────────────────────────

    def test_scopes_match_the_oauth_client_declaration(self):
        self.assertEqual(
            set(YouTubeService.SCOPES),
            {UPLOAD_SCOPE, READONLY_SCOPE, USERINFO_SCOPE},
        )

    def test_auth_url_requests_all_declared_scopes(self):
        url = self.service.get_auth_url("state-token", code_verifier=generate_code_verifier())
        qs = parse_qs(urlparse(url).query)
        requested = set(qs["scope"][0].split())
        self.assertEqual(requested, set(YouTubeService.SCOPES))
        self.assertIn(USERINFO_SCOPE, requested)

    # ── Account info (userinfo.profile enrichment) ───────────────────────────

    def test_channel_info_includes_google_profile_when_scope_granted(self):
        self.client.channel = {"items": [{"id": "UC1", "snippet": {"title": "My Channel"}}]}
        self.client.profile = {
            "names": [{"displayName": "Jane Doe"}],
            "emailAddresses": ["jane@gmail.com"],
            "photos": [{"url": "https://lh3.googleusercontent.com/a/pic"}],
        }

        info = asyncio.run(self.service.get_channel_info("at", "rt"))

        self.assertEqual(info["account_id"], "UC1")
        self.assertEqual(info["account_name"], "My Channel")
        self.assertEqual(
            info["extra_data"],
            {
                "google_name": "Jane Doe",
                "google_email": "jane@gmail.com",
                "google_picture": "https://lh3.googleusercontent.com/a/pic",
            },
        )
        # Channel from youtube v3, profile from people v1.
        self.assertIn(("youtube", "v3"), self._built)
        self.assertIn(("people", "v1"), self._built)

    def test_channel_info_succeeds_when_people_api_fails(self):
        # Connections made before the scope change hold tokens without
        # userinfo.profile — the People API rejects them. The connect must
        # still succeed, just without the profile metadata.
        self.client.channel = {"items": [{"id": "UC2", "snippet": {"title": "Old Channel"}}]}
        self.client.profile_error = RuntimeError("403: People API not enabled for this project")

        info = asyncio.run(self.service.get_channel_info("at", "rt"))

        self.assertEqual(info["account_id"], "UC2")
        self.assertEqual(info["account_name"], "Old Channel")
        self.assertEqual(info["extra_data"], {})

    def test_channel_info_still_fails_without_a_channel(self):
        self.client.channel = {"items": []}

        with self.assertRaisesRegex(Exception, "no YouTube channel"):
            asyncio.run(self.service.get_channel_info("at", "rt"))

    # ── Upload (unchanged API surface, refactored client) ────────────────────

    def test_upload_short_uses_youtube_v3_and_marks_the_title(self):
        path = os.path.join(tempfile.mkdtemp(prefix="le-yt-up-"), "clip.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 32)

        result = asyncio.run(
            self.service.upload_short(path, "My clip", "some description", "at")
        )

        self.assertEqual(result["video_id"], "vid-123")
        self.assertEqual(result["video_url"], "https://www.youtube.com/shorts/vid-123")
        self.assertEqual(self._built, [("youtube", "v3")])
        self.assertEqual(self.client.last_upload_body["snippet"]["title"], "My clip #Shorts")


if __name__ == "__main__":
    unittest.main()

"""YouTube playlists: listing a channel's collections and filing a Short into them.

Two service calls back the upload editor's "Add to YouTube playlists" picker:

* ``list_playlists`` — ``playlists.list?mine=true`` for the connected channel,
  mapped to the small shape the picker renders (id/title/privacy/item_count)
  and paginated, because a channel with more than 50 playlists would otherwise
  show a truncated list with no way to reach the rest.
* ``add_to_playlists`` — one ``playlistItems.insert`` per chosen playlist, run
  *after* the upload. The video is public by then, so a playlist problem must
  be reported per playlist and never raised: the worker turns the return value
  into a note on an otherwise successful post.

The scope set gained ``https://www.googleapis.com/auth/youtube`` because
``youtube.readonly`` can list playlists but cannot insert into one; that is
pinned here too, since dropping it would silently break every insert with a
403 that the user cannot diagnose.
"""
import asyncio
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from googleapiclient.errors import HttpError  # noqa: E402

from services.social.youtube import YouTubeService  # noqa: E402

WRITE_SCOPE = "https://www.googleapis.com/auth/youtube"
UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
USERINFO_SCOPE = "https://www.googleapis.com/auth/userinfo.profile"


class _Request:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


def http_error(status, reason, message):
    """A real ``googleapiclient.errors.HttpError`` — the service catches that type."""

    class _Resp:
        pass

    resp = _Resp()
    resp.status = status
    resp.reason = message
    content = json.dumps(
        {"error": {"code": status, "message": message, "errors": [{"reason": reason, "message": message}]}}
    ).encode("utf-8")
    return HttpError(resp, content)


class FakePlaylistClient:
    """Stands in for the discovery client's ``playlists`` / ``playlistItems``."""

    def __init__(self, pages=None, insert_results=None):
        # One entry per ``playlists.list`` page, in order.
        self.pages = pages if pages is not None else [{"items": []}]
        # playlist_id -> response dict, or an exception to raise for it.
        self.insert_results = insert_results or {}
        self.list_calls = []
        self.insert_bodies = []
        self._page = 0

    def playlists(self):
        outer = self

        class _Playlists:
            def list(self, part=None, mine=None, maxResults=None, pageToken=None):
                outer.list_calls.append(
                    {"part": part, "mine": mine, "maxResults": maxResults, "pageToken": pageToken}
                )
                page = outer.pages[min(outer._page, len(outer.pages) - 1)]
                outer._page += 1
                return _Request(page)

        return _Playlists()

    def playlistItems(self):
        outer = self

        class _PlaylistItems:
            def insert(self, part=None, body=None):
                outer.insert_bodies.append(body)
                playlist_id = (body or {}).get("snippet", {}).get("playlistId")
                result = outer.insert_results.get(playlist_id, {"id": f"item-{playlist_id}"})
                if isinstance(result, Exception):
                    raise result
                return _Request(result)

        return _PlaylistItems()


def playlist(playlist_id, title, privacy="public", item_count=0):
    return {
        "id": playlist_id,
        "snippet": {"title": title},
        "status": {"privacyStatus": privacy},
        "contentDetails": {"itemCount": item_count},
    }


class YouTubePlaylistTests(unittest.TestCase):
    def setUp(self):
        self.client = FakePlaylistClient()
        # _client is a class-level function, so the replacement takes the
        # instance first; the fake is returned from the tuple trick.
        self._client_patcher = patch.object(
            YouTubeService,
            "_client",
            lambda service, name, version, access_token, refresh_token: self.client,
        )
        self._client_patcher.start()
        self.service = YouTubeService()

    def tearDown(self):
        self._client_patcher.stop()

    # ── Scopes ───────────────────────────────────────────────────────────────

    def test_scopes_include_playlist_write_access(self):
        self.assertEqual(
            set(YouTubeService.SCOPES),
            {UPLOAD_SCOPE, READONLY_SCOPE, WRITE_SCOPE, USERINFO_SCOPE},
        )

    def test_auth_url_requests_the_playlist_scope(self):
        """The consent screen has to ask for it, or every insert 403s later."""
        from urllib.parse import parse_qs, urlparse

        with patch.multiple(
            __import__("core.config", fromlist=["settings"]).settings,
            YOUTUBE_CLIENT_ID="yt-id",
            YOUTUBE_CLIENT_SECRET="yt-secret",
            YOUTUBE_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback",
        ):
            url = self.service.get_auth_url("state-token", code_verifier="verifier")
        self.assertIn(WRITE_SCOPE, set(parse_qs(urlparse(url).query)["scope"][0].split()))

    # ── list_playlists ───────────────────────────────────────────────────────

    def test_lists_the_connected_channels_own_playlists(self):
        self.client.pages = [
            {
                "items": [
                    playlist("PL1", "Morning Routine", "public", 12),
                    playlist("PL2", "Unlisted tests", "unlisted", 3),
                ]
            }
        ]
        result = asyncio.run(self.service.list_playlists("access", "refresh"))

        self.assertEqual(
            result,
            [
                {"id": "PL1", "title": "Morning Routine", "privacy": "public", "item_count": 12},
                {"id": "PL2", "title": "Unlisted tests", "privacy": "unlisted", "item_count": 3},
            ],
        )
        # mine=True and nothing else: a user picks their own playlists only.
        self.assertTrue(self.client.list_calls[0]["mine"])
        self.assertIn("snippet", self.client.list_calls[0]["part"])

    def test_paginates_past_the_first_page(self):
        self.client.pages = [
            {"items": [playlist(f"PL{i}", f"P{i}") for i in range(3)], "nextPageToken": "tok-2"},
            {"items": [playlist("PL9", "Last one")]},
        ]
        result = asyncio.run(self.service.list_playlists("access"))

        self.assertEqual([item["id"] for item in result], ["PL0", "PL1", "PL2", "PL9"])
        self.assertEqual(self.client.list_calls[1]["pageToken"], "tok-2")

    def test_tolerates_missing_optional_fields(self):
        """A playlist with no title/status must still be selectable."""
        self.client.pages = [{"items": [{"id": "PL5"}]}]
        result = asyncio.run(self.service.list_playlists("access"))
        self.assertEqual(result, [{"id": "PL5", "title": "(untitled playlist)", "privacy": "", "item_count": 0}])

    def test_skips_items_without_an_id(self):
        self.client.pages = [{"items": [{"snippet": {"title": "no id"}}, playlist("PL7", "Real")]}]
        result = asyncio.run(self.service.list_playlists("access"))
        self.assertEqual([item["id"] for item in result], ["PL7"])

    def test_reports_a_google_refusal_readably(self):
        self.client.pages = None
        with patch.object(
            FakePlaylistClient, "playlists", side_effect=http_error(403, "forbidden", "Access Not Configured")
        ):
            with self.assertRaises(Exception) as caught:
                asyncio.run(self.service.list_playlists("access"))
        message = str(caught.exception)
        self.assertIn("playlist list failed", message)
        self.assertIn("Access Not Configured", message)
        # The raw "<HttpError 403 ...>" repr is what the old code showed.
        self.assertNotIn("<HttpError", message)

    # ── add_to_playlists ─────────────────────────────────────────────────────

    def test_inserts_the_video_into_every_chosen_playlist(self):
        result = asyncio.run(self.service.add_to_playlists("vid-1", ["PL1", "PL2"], "access", "refresh"))

        self.assertEqual(result, {"added": ["PL1", "PL2"], "failed": []})
        self.assertEqual(
            self.client.insert_bodies,
            [
                {"snippet": {"playlistId": "PL1", "resourceId": {"kind": "youtube#video", "videoId": "vid-1"}}},
                {"snippet": {"playlistId": "PL2", "resourceId": {"kind": "youtube#video", "videoId": "vid-1"}}},
            ],
        )

    def test_one_bad_playlist_does_not_stop_the_others(self):
        """The upload already succeeded — a missing playlist is a note, not a failure."""
        self.client.insert_results = {
            "PL1": http_error(404, "playlistNotFound", "Playlist not found."),
        }
        result = asyncio.run(self.service.add_to_playlists("vid-1", ["PL1", "PL2"], "access"))

        self.assertEqual(result["added"], ["PL2"])
        self.assertEqual(len(result["failed"]), 1)
        failure = result["failed"][0]
        self.assertEqual(failure["playlist_id"], "PL1")
        self.assertIn("Playlist not found.", failure["error"])
        self.assertIn("playlistNotFound", failure["error"])

    def test_expired_scope_is_explained_as_a_reconnect(self):
        """A connection made before the scope change needs to reconnect, and the
        message has to say so rather than dumping an OAuth payload."""
        self.client.insert_results = {"PL1": http_error(403, "forbidden", "The caller does not have permission")}
        result = asyncio.run(self.service.add_to_playlists("vid-1", ["PL1"], "access"))
        self.assertEqual(result["added"], [])
        self.assertIn("playlist access", result["failed"][0]["error"])
        self.assertIn("Reconnect YouTube", result["failed"][0]["error"])

    def test_blank_and_duplicate_ids_are_ignored(self):
        result = asyncio.run(self.service.add_to_playlists("vid-1", [" PL1 ", "PL1", "", None], "access"))
        self.assertEqual(result["added"], ["PL1"])
        self.assertEqual(len(self.client.insert_bodies), 1)

    def test_no_playlists_selected_is_a_no_op(self):
        """Nothing to do must not build a client or call Google at all."""
        with patch.object(YouTubeService, "_client", side_effect=AssertionError("client built for nothing")):
            result = asyncio.run(self.service.add_to_playlists("vid-1", [], "access"))
        self.assertEqual(result, {"added": [], "failed": []})

    def test_missing_video_id_is_a_no_op(self):
        with patch.object(YouTubeService, "_client", side_effect=AssertionError("client built for nothing")):
            result = asyncio.run(self.service.add_to_playlists("", ["PL1"], "access"))
        self.assertEqual(result, {"added": [], "failed": []})

    def test_unexpected_exception_is_captured_per_playlist(self):
        self.client.insert_results = {"PL1": RuntimeError("connection reset")}
        result = asyncio.run(self.service.add_to_playlists("vid-1", ["PL1"], "access"))
        self.assertEqual(result["added"], [])
        self.assertEqual(result["failed"][0]["error"], "connection reset")


if __name__ == "__main__":
    unittest.main()

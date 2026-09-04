"""Instagram Reels: publishing the local file directly, without a public URL.

Instagram's Graph API has two ways to hand it a video:

* ``POST /<ig-user>/media`` with ``video_url`` — Meta's crawler downloads the
  file, so it must be reachable from the public internet. An instance running
  on a laptop produces ``http://localhost:8000/...`` (or nothing at all), which
  is why Reels publishing used to hard-fail unless ``PUBLIC_API_URL`` pointed
  at a tunnel such as ngrok.
* the resumable upload — create the container with ``upload_type=resumable``,
  then POST the bytes to the ``rupload.facebook.com`` URI the response
  contains, with ``Authorization: OAuth <token>``, ``offset`` and
  ``file_size`` headers. The file never has to be public.

``publish_reel(video_path=...)`` uses the second one and keeps the first as a
fallback for an instance that has a real public URL and has turned direct
upload off (``INSTAGRAM_DIRECT_UPLOAD=false``). These tests pin the request
shape Meta documents, the fallback decision, and the error text for the one
case where neither can work.
"""
import asyncio
import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from core.config import settings  # noqa: E402
from services.social import instagram as instagram_module  # noqa: E402
from services.social.instagram import InstagramService, is_public_video_url  # noqa: E402

GRAPH = InstagramService.GRAPH_API
IG_USER = "17841400000000000"
TOKEN = "ig-access-token"
RUPLOAD_URI = f"https://rupload.facebook.com/ig-api-upload/v25.0/container-1"
VIDEO_BYTES = b"\x00\x01fake-mp4-payload"


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, **_kwargs):
        # aiohttp's json() takes content_type=…; the service passes it.
        return self._payload

    async def text(self, **_kwargs):
        # The upload step reads the raw body: Meta's upload host is not
        # reliably JSON (an empty 200 and proxy error pages both happen).
        payload = self._payload
        if isinstance(payload, (dict, list)):
            return json.dumps(payload)
        return "" if payload is None else str(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _resolve(route):
    """A route value is either a payload or a ``(status, payload)`` pair."""
    if isinstance(route, tuple):
        return _Response(route[1], status=route[0])
    return _Response(route)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeUploadSession:
    """Async-context-manager stand-in for ``aiohttp.ClientSession``.

    ``post_routes`` maps an exact URL to the payload that POST returns; every
    request (GET or POST) is recorded in ``calls`` with its params, headers and
    body so tests can assert the exact request Meta expects. File objects are
    read and closed by the session, exactly like aiohttp does.
    """

    def __init__(self, post_routes=None, get_routes=None):
        self.post_routes = post_routes or {}
        self.get_routes = get_routes or {}
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def _record(self, method, url, params, headers, body):
        self.calls.append({"method": method, "url": url, "params": dict(params or {}), "headers": dict(headers or {}), "body": body})

    def get(self, url, params=None, headers=None, **_):
        self._record("GET", url, params, headers, None)
        if url not in self.get_routes:
            raise AssertionError(f"unexpected GET {url}; routed: {sorted(self.get_routes)}")
        return _resolve(self.get_routes[url])

    def post(self, url, data=None, params=None, headers=None, **_):
        body = None
        if hasattr(data, "read"):  # a file object, as the direct upload sends
            body = data.read()
            data.close()
        else:
            body = data
        self._record("POST", url, params, headers, body)
        if url not in self.post_routes:
            raise AssertionError(f"unexpected POST {url}; routed: {sorted(self.post_routes)}")
        return _resolve(self.post_routes[url])

    def posts_to(self, suffix):
        return [call for call in self.calls if call["method"] == "POST" and call["url"].endswith(suffix)]


class DirectUploadTests(unittest.TestCase):
    def setUp(self):
        self._settings = patch.multiple(
            settings,
            INSTAGRAM_APP_ID="ig-app-id",
            INSTAGRAM_APP_SECRET="ig-app-secret",
            INSTAGRAM_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/instagram/callback",
        )
        self._settings.start()
        self.service = InstagramService()
        self._tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        self._tmp.write(VIDEO_BYTES)
        self._tmp.close()

    def tearDown(self):
        os.unlink(self._tmp.name)
        self._settings.stop()

    def _routes(self, **overrides):
        routes = {
            f"{GRAPH}/{IG_USER}/media": {"id": "container-1", "uri": RUPLOAD_URI},
            RUPLOAD_URI: {"success": True, "message": "Upload successful."},
            f"{GRAPH}/{IG_USER}/media_publish": {"id": "media-1"},
        }
        routes.update(overrides.pop("post", {}) if isinstance(overrides.get("post"), dict) else {})
        gets = {
            # the container's processing status (direct flow) / the same shape
            # the URL flow polls on its container id
            f"{GRAPH}/container-1": {
                "id": "container-1",
                "status_code": "FINISHED",
                "video_status": {"processing_phase": {"status": "published"}},
            },
            # permalink lookup for the published media object
            f"{GRAPH}/media-1": {"id": "media-1", "permalink": "https://www.instagram.com/reel/media-1/"},
        }
        gets.update(overrides.pop("get", {}) if isinstance(overrides.get("get"), dict) else {})
        return FakeUploadSession(post_routes=routes, get_routes=gets)

    def _run(self, session, **kwargs):
        with patch.object(instagram_module.aiohttp, "ClientSession", return_value=session):
            return asyncio.run(
                self.service.publish_reel(
                    ig_user_id=kwargs.pop("ig_user_id", IG_USER),
                    video_url=kwargs.pop("video_url", ""),
                    caption=kwargs.pop("caption", ""),
                    access_token=kwargs.pop("access_token", TOKEN),
                    **kwargs,
                )
            )

    # ── Direct upload ────────────────────────────────────────────────────────

    def test_publishes_the_local_file_without_a_public_url(self):
        session = self._routes()
        result = self._run(session, video_path=self._tmp.name, caption="New reel #shorts")

        self.assertEqual(result, {"media_id": "media-1", "post_url": "https://www.instagram.com/reel/media-1/"})

        # 1. container creation asks for the resumable flow
        container = session.posts_to(f"/{IG_USER}/media")[0]
        self.assertEqual(container["body"]["upload_type"], "resumable")
        # REELS, not VIDEO: a Reel container is what the /media endpoint
        # expects here, and the same value the URL flow has always sent.
        self.assertEqual(container["body"]["media_type"], "REELS")
        self.assertEqual(container["body"]["access_token"], TOKEN)
        self.assertNotIn("video_url", container["body"])

        # 2. the bytes go to the rupload URI from that response
        upload = session.calls[1]
        self.assertEqual(upload["url"], RUPLOAD_URI)
        self.assertEqual(upload["body"], VIDEO_BYTES)
        self.assertEqual(upload["headers"]["Authorization"], f"OAuth {TOKEN}")
        self.assertEqual(upload["headers"]["offset"], "0")
        self.assertEqual(upload["headers"]["file_size"], str(len(VIDEO_BYTES)))

        # 3. publish references the container
        publish = session.posts_to(f"/{IG_USER}/media_publish")[0]
        self.assertEqual(publish["body"]["creation_id"], "container-1")

    def test_caption_is_set_at_container_creation_not_publish(self):
        """Reels carry the caption on the container; sending it again on
        media_publish is what produced 'invalid parameter' failures."""
        session = self._routes()
        self._run(session, video_path=self._tmp.name, caption="Caption here")

        container = session.posts_to(f"/{IG_USER}/media")[0]
        self.assertEqual(container["body"]["caption"], "Caption here")
        publish = session.posts_to(f"/{IG_USER}/media_publish")[0]
        self.assertNotIn("caption", publish["body"])

    def test_a_missing_file_is_reported_before_any_request(self):
        session = self._routes()
        with self.assertRaises(FileNotFoundError):
            self._run(session, video_path="/nonexistent/reel.mp4")
        self.assertEqual(session.calls, [], "no HTTP call should be made for a missing file")

    def test_no_file_and_a_localhost_url_explains_both_options(self):
        session = self._routes()
        with self.assertRaises(Exception) as caught:
            self._run(session, video_url="http://localhost:8000/uploads/x.mp4")
        message = str(caught.exception)
        # The message has to name both ways out, because from here the user
        # cannot tell which of the two inputs is missing.
        self.assertIn("no publicly reachable video URL", message)
        self.assertIn("PUBLIC_API_URL", message)
        self.assertIn("restore the uploaded file", message)
        self.assertEqual(session.calls, [])

    def test_waits_for_both_container_and_processing_status(self):
        session = self._routes()
        self._run(session, video_path=self._tmp.name)
        status_call = [call for call in session.calls if call["method"] == "GET"][0]
        self.assertEqual(status_call["url"], f"{GRAPH}/container-1")
        self.assertIn("status_code", status_call["params"]["fields"])
        self.assertIn("video_status", status_call["params"]["fields"])

    def test_an_upload_rejection_is_surfaced(self):
        session = self._routes(
            post={
                RUPLOAD_URI: (
                    400,
                    {
                        "error": {"message": "Upload phase failed", "type": "OAuthException"},
                        "debug_info": {"retriable": False, "type": "ProcessingFailedError", "message": "Video file is too large"},
                    },
                )
            }
        )
        with self.assertRaises(Exception) as caught:
            self._run(session, video_path=self._tmp.name)
        self.assertIn("Video file is too large", str(caught.exception))
        self.assertIn("HTTP 400", str(caught.exception))
        # The container was created but nothing was published.
        self.assertEqual(session.posts_to(f"/{IG_USER}/media_publish"), [])

    def test_an_empty_upload_response_is_not_treated_as_a_failure(self):
        """Some networks answer the upload with an empty 200. The container
        status poll is the real gate, so this must go on to publish."""
        session = self._routes(post={RUPLOAD_URI: ""})
        result = self._run(session, video_path=self._tmp.name)
        self.assertEqual(result["media_id"], "media-1")
        self.assertEqual(len(session.posts_to(f"/{IG_USER}/media_publish")), 1)

    def test_a_non_json_error_page_is_reported_with_its_status(self):
        session = self._routes(post={RUPLOAD_URI: (502, "<html>Bad Gateway</html>")})
        with self.assertRaises(Exception) as caught:
            self._run(session, video_path=self._tmp.name)
        self.assertIn("HTTP 502", str(caught.exception))
        self.assertIn("Bad Gateway", str(caught.exception))

    # ── URL fallback ─────────────────────────────────────────────────────────

    def test_falls_back_to_the_public_url_when_no_local_file_exists(self):
        session = self._routes()
        result = self._run(session, video_url="https://cdn.example.com/videos/x.mp4", caption="From a URL")

        self.assertEqual(result["media_id"], "media-1")
        container = session.posts_to(f"/{IG_USER}/media")[0]
        self.assertEqual(container["body"]["video_url"], "https://cdn.example.com/videos/x.mp4")
        self.assertNotIn("upload_type", container["body"])
        # No rupload call at all — Meta fetches the file itself.
        self.assertEqual([call for call in session.calls if "rupload" in call["url"]], [])

    # ── is_public_video_url ──────────────────────────────────────────────────

    def test_only_a_genuinely_reachable_url_counts_as_public(self):
        for url in [
            "",
            None,
            "http://localhost:8000/uploads/x.mp4",
            "https://localhost:8000/uploads/x.mp4",
            "http://127.0.0.1:8000/uploads/x.mp4",
            "http://0.0.0.0:8000/uploads/x.mp4",
            "http://192.168.1.20:8000/uploads/x.mp4",  # the laptop's LAN address
            "http://10.0.0.9/uploads/x.mp4",
            "http://[::1]:8000/uploads/x.mp4",
            "ftp://cdn.example.com/x.mp4",
            "not a url",
        ]:
            self.assertFalse(is_public_video_url(url), url)
        for url in ["https://cdn.example.com/x.mp4", "http://video.akamaihd.net/x.mp4"]:
            self.assertTrue(is_public_video_url(url), url)


if __name__ == "__main__":
    unittest.main()

"""Social scheduler API: YouTube playlist targets on a post, and the picker route.

Two surfaces back the upload editor's "Add to YouTube playlists" picker:

* ``POST/PATCH /posts`` accept ``youtube_playlist_ids`` and store them so the
  worker can file the Short into them after the upload. Validation has to
  survive a hand-built request (the picker cannot select the same playlist
  twice, an HTTP client can), and a stale or absent selection must simply mean
  "publish without adding it anywhere".
* ``GET /platforms/youtube/playlists`` lists the connected channel's
  playlists. It is per user, needs a connected YouTube account, and reports
  upstream trouble as a 502/409 the page can show inline — never a 500, and
  never another user's channel.

Runs against the real router on an in-memory database, like
tests/test_social_scheduler_api.py.
"""
import asyncio
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1 import social_scheduler as module  # noqa: E402
from api.v1.social_scheduler import router  # noqa: E402
from core.config import settings  # noqa: E402
from core.security import encrypt_credential  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.social_scheduler import SocialPlatformConnection, SocialPost, SocialPostResult  # noqa: E402
from models.user import User  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

OWNER = "owner@test.dev"
OTHER = "other@test.dev"


def _user(email):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role="customer")


class FakeYouTubeService:
    """Stands in for YouTubeService: the picker only lists (and may refresh)."""

    def __init__(self, playlists=None, error=None):
        self.playlists = playlists if playlists is not None else []
        self.error = error
        self.list_calls = []
        self.refresh_calls = []

    async def list_playlists(self, access_token, refresh_token=None, **_kwargs):
        self.list_calls.append({"access_token": access_token, "refresh_token": refresh_token})
        if self.error is not None:
            raise self.error
        return self.playlists

    async def refresh_access_token(self, refresh_token, current_access_token=None):
        self.refresh_calls.append({"refresh_token": refresh_token, "current": current_access_token})
        return {"access_token": "renewed-token", "refresh_token": None, "expires_in": 3600}


class PlaylistApiTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.upload_dir = tempfile.mkdtemp(prefix="le-playlists-")
        self._settings_patch = patch.multiple(
            settings,
            UPLOAD_DIR=self.upload_dir,
            MAX_UPLOAD_SIZE=1024 * 1024,
            YOUTUBE_CLIENT_ID="yt-id",
            YOUTUBE_CLIENT_SECRET="yt-secret",
            YOUTUBE_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback",
            PUBLIC_API_URL="https://api.example.com",
        )
        self._settings_patch.start()

        app = FastAPI()
        app.include_router(router)
        self.current_email = OWNER

        async def override_get_db():
            async with self.Session() as session:
                yield session

        async def override_user():
            async with self.Session() as session:
                return (await session.execute(select(User).where(User.email == self.current_email))).scalar_one()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_user
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        self._settings_patch.stop()
        shutil.rmtree(self.upload_dir, ignore_errors=True)
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as s:
            s.add_all([_user(OWNER), _user(OTHER)])
            await s.commit()

    def run_async(self, fn):
        async def runner():
            async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                return await fn(client)

        return self.loop.run_until_complete(runner())

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _upload_id(self, client):
        res = await client.post(
            "/api/v1/social-scheduler/upload", files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")}
        )
        assert res.status_code == 200, res.text
        return res.json()["upload_id"]

    async def _connect_youtube(self, email=OWNER, *, expires_at=None):
        async with self.Session() as s:
            s.add(
                SocialPlatformConnection(
                    owner_email=email,
                    platform="youtube",
                    encrypted_access_token=encrypt_credential("yt-access"),
                    encrypted_refresh_token=encrypt_credential("yt-refresh"),
                    account_id="channel-1",
                    account_name="Test Channel",
                    expires_at=expires_at,
                )
            )
            await s.commit()

    async def _create(self, client, upload_id, **overrides):
        body = {
            "title": "Launch teaser",
            "caption": "We're live",
            "hashtags": "#launch",
            "upload_id": upload_id,
            "platforms": ["youtube"],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        body.update(overrides)
        return await client.post("/api/v1/social-scheduler/posts", json=body)

    # ── storing the selection on a post ──────────────────────────────────────

    def test_playlist_ids_are_stored_and_returned(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id, youtube_playlist_ids=["PLabc", "PLxyz"])
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(res.json()["youtube_playlist_ids"], ["PLabc", "PLxyz"])

            fetched = await client.get(f"/api/v1/social-scheduler/posts/{res.json()['id']}")
            self.assertEqual(fetched.json()["youtube_playlist_ids"], ["PLabc", "PLxyz"])

        self.run_async(run)

    def test_defaults_to_no_playlists(self):
        """Posts created before this feature (or without a selection) publish
        exactly as they did — an empty list, never None."""

        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id)
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(res.json()["youtube_playlist_ids"], [])

        self.run_async(run)

    def test_blank_and_duplicate_ids_are_cleaned(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id, youtube_playlist_ids=[" PLabc ", "PLabc", "", "PLxyz"])
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(res.json()["youtube_playlist_ids"], ["PLabc", "PLxyz"])

        self.run_async(run)

    def test_more_than_ten_playlists_is_rejected(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id, youtube_playlist_ids=[f"PL{i}" for i in range(11)])
            self.assertEqual(res.status_code, 422)
            self.assertIn("at most 10", res.text)

        self.run_async(run)

    def test_a_ridiculous_id_is_rejected(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id, youtube_playlist_ids=["x" * 101])
            self.assertEqual(res.status_code, 422)

        self.run_async(run)

    def test_selection_can_be_edited_and_cleared(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            created = await self._create(client, upload_id, youtube_playlist_ids=["PLabc"])
            post_id = created.json()["id"]

            edited = await client.patch(
                f"/api/v1/social-scheduler/posts/{post_id}", json={"youtube_playlist_ids": ["PLnew", "PLtwo"]}
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()["youtube_playlist_ids"], ["PLnew", "PLtwo"])

            cleared = await client.patch(
                f"/api/v1/social-scheduler/posts/{post_id}", json={"youtube_playlist_ids": []}
            )
            self.assertEqual(cleared.json()["youtube_playlist_ids"], [])

        self.run_async(run)

    def test_another_users_post_cannot_be_given_playlists(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            created = await self._create(client, upload_id, youtube_playlist_ids=["PLabc"])
            post_id = created.json()["id"]

            self.current_email = OTHER
            res = await client.patch(
                f"/api/v1/social-scheduler/posts/{post_id}", json={"youtube_playlist_ids": ["PLevil"]}
            )
            self.assertEqual(res.status_code, 404)  # no existence oracle

        self.run_async(run)

    # ── the picker route ─────────────────────────────────────────────────────

    def test_playlists_require_a_connected_youtube_account(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")
            self.assertEqual(res.status_code, 409)
            self.assertIn("not connected", res.json()["detail"])

        self.run_async(run)

    def test_lists_the_connected_channels_playlists(self):
        async def run(client):
            await self._connect_youtube()
            service = FakeYouTubeService(
                playlists=[
                    {"id": "PLabc", "title": "Morning Routine", "privacy": "public", "item_count": 12},
                    {"id": "PLxyz", "title": "Tests", "privacy": "unlisted", "item_count": 0},
                ]
            )
            with patch.object(module, "get_service", return_value=service):
                res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")

            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(body["channel"], "Test Channel")
            self.assertEqual([p["id"] for p in body["playlists"]], ["PLabc", "PLxyz"])
            self.assertEqual(body["playlists"][0]["item_count"], 12)
            # the caller's own stored token was used, decrypted
            self.assertEqual(service.list_calls[0]["access_token"], "yt-access")
            self.assertEqual(service.list_calls[0]["refresh_token"], "yt-refresh")

        self.run_async(run)

    def test_an_expired_token_is_renewed_before_listing(self):
        async def run(client):
            await self._connect_youtube(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
            service = FakeYouTubeService(playlists=[{"id": "PLabc", "title": "P", "privacy": "public", "item_count": 1}])
            with patch.object(module, "get_service", return_value=service):
                res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")

            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(service.refresh_calls[0]["current"], "yt-access")
            self.assertEqual(service.list_calls[0]["access_token"], "renewed-token")

        self.run_async(run)

    def test_an_unrenewable_token_is_a_409_telling_the_user_to_reconnect(self):
        async def run(client):
            await self._connect_youtube(expires_at=datetime.now(timezone.utc) - timedelta(hours=1))
            service = FakeYouTubeService(playlists=[])
            service.refresh_access_token = _raise(Exception("invalid_grant"))
            with patch.object(module, "get_service", return_value=service):
                res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")

            self.assertEqual(res.status_code, 409)
            self.assertIn("could not be renewed", res.json()["detail"])

        self.run_async(run)

    def test_a_google_refusal_is_a_502_not_a_500(self):
        async def run(client):
            await self._connect_youtube()
            service = FakeYouTubeService(error=Exception("YouTube playlist list failed: quotaExceeded"))
            with patch.object(module, "get_service", return_value=service):
                res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")

            self.assertEqual(res.status_code, 502)
            self.assertIn("quotaExceeded", res.json()["detail"])

        self.run_async(run)

    def test_playlists_are_scoped_to_the_caller(self):
        """Another user's YouTube connection must not feed this user's picker."""

        async def run(client):
            await self._connect_youtube(email=OTHER)
            res = await client.get("/api/v1/social-scheduler/platforms/youtube/playlists")
            self.assertEqual(res.status_code, 409)

        self.run_async(run)

    # ── the result note ──────────────────────────────────────────────────────

    def test_a_publish_note_is_returned_with_the_result(self):
        """'Published, but 1 playlist could not be updated' has to survive the
        round trip — it is not an error, so `error` would not be rendered."""

        async def run(client):
            upload_id = await self._upload_id(client)
            created = await self._create(client, upload_id, youtube_playlist_ids=["PLabc"])
            post_id = created.json()["id"]
            async with self.Session() as s:
                s.add(
                    SocialPostResult(
                        post_id=post_id,
                        owner_email=OWNER,
                        platform="youtube",
                        status="posted",
                        platform_id="vid-1",
                        platform_url="https://youtu.be/vid-1",
                        note="Published, and added to 1 of 2 playlists. YouTube Shorts could not update: PLold: Playlist not found.",
                    )
                )
                await s.commit()

            res = await client.get(f"/api/v1/social-scheduler/posts/{post_id}")
            self.assertEqual(res.status_code, 200, res.text)
            result = res.json()["results"][0]
            self.assertEqual(result["status"], "posted")
            self.assertEqual(result["error"], "")
            self.assertIn("added to 1 of 2 playlists", result["note"])

        self.run_async(run)


def _raise(exc):
    async def _fn(*_args, **_kwargs):
        raise exc

    return _fn


if __name__ == "__main__":
    unittest.main()

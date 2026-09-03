"""Social scheduler API: per-user scoping, upload safety, OAuth, encryption.

The standalone service had no authentication and one shared platform
connection per deployment. After the merge every route runs as the
authenticated LinkEasy user; these tests pin that down end to end against
the real router on an in-memory database:

  * two users never see, edit or delete each other's posts or connections;
  * a post can only reference a video the server itself stored (upload_id),
    never a caller-chosen filesystem path;
  * the OAuth callback accepts only the signed state minted for that user
    and platform, and stores tokens encrypted;
  * stats and calendar aggregate the caller's rows only.
"""
import asyncio
import base64
import hashlib
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

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
from jose import jwt  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1 import social_scheduler as module  # noqa: E402
from api.v1.social_scheduler import router  # noqa: E402
from core.config import settings  # noqa: E402
from core.security import decrypt_credential, encrypt_credential  # noqa: E402
from database import Base  # noqa: E402
from services.social.pkce import is_valid_code_verifier  # noqa: E402
import models  # noqa: E402,F401
from models.social_scheduler import SocialPlatformConnection, SocialPost, SocialPostResult  # noqa: E402
from models.user import User  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

OWNER = "owner@test.dev"
OTHER = "other@test.dev"


def _user(email):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role="customer")


class SocialSchedulerApiTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.upload_dir = tempfile.mkdtemp(prefix="le-social-")
        self._settings_patch = patch.multiple(
            settings,
            UPLOAD_DIR=self.upload_dir,
            MAX_UPLOAD_SIZE=1024 * 1024,
            YOUTUBE_CLIENT_ID="yt-id",
            YOUTUBE_CLIENT_SECRET="yt-secret",
            YOUTUBE_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback",
            INSTAGRAM_APP_ID="",
            INSTAGRAM_APP_SECRET="",
            TIKTOK_CLIENT_KEY="tt-key",
            TIKTOK_CLIENT_SECRET="tt-secret",
            PUBLIC_API_URL="https://api.example.com",
            SOCIAL_OAUTH_RETURN_URL="http://localhost:5173/app/social-scheduler/settings",
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

    async def _upload(self, client, name="clip.mp4", content=b"\x00" * 2048, content_type="video/mp4"):
        return await client.post(
            "/api/v1/social-scheduler/upload", files={"file": (name, content, content_type)}
        )

    async def _create_post(self, client, upload_id, **overrides):
        body = {
            "title": "Launch teaser",
            "caption": "We're live",
            "hashtags": "#launch",
            "upload_id": upload_id,
            "platforms": ["youtube", "tiktok"],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        body.update(overrides)
        return await client.post("/api/v1/social-scheduler/posts", json=body)

    # ── upload ───────────────────────────────────────────────────────────────

    def test_upload_stores_under_a_server_generated_name(self):
        async def run(client):
            res = await self._upload(client, name="../../etc/passwd.mp4")
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertRegex(data["upload_id"], r"^[0-9a-f]{32}\.mp4$")
            self.assertEqual(data["size_bytes"], 2048)
            self.assertEqual(data["video_url"], f"https://api.example.com/uploads/social/{data['upload_id']}")
            self.assertTrue(os.path.isfile(os.path.join(self.upload_dir, data["upload_id"])))
            self.assertEqual(os.listdir(self.upload_dir), [data["upload_id"]])

        self.run_async(run)

    def test_upload_rejects_non_video_and_oversized_files(self):
        async def run(client):
            res = await self._upload(client, name="notes.txt", content=b"hello", content_type="text/plain")
            self.assertEqual(res.status_code, 400)
            res = await self._upload(client, content=b"\x00" * (1024 * 1024 + 1))
            self.assertEqual(res.status_code, 413)
            res = await self._upload(client, content=b"")
            self.assertEqual(res.status_code, 400)
            self.assertEqual(os.listdir(self.upload_dir), [], "rejected uploads must not leave files behind")

        self.run_async(run)

    # ── posts ────────────────────────────────────────────────────────────────

    def test_post_uses_the_server_side_path_never_a_client_path(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            res = await self._create_post(client, upload_id)
            self.assertEqual(res.status_code, 201, res.text)
            data = res.json()
            self.assertNotIn("video_path", data, "the filesystem path must never be exposed")
            self.assertEqual(data["status"], "pending")
            self.assertEqual(data["platforms"], ["youtube", "tiktok"])
            self.assertEqual(data["results"], [])

            async with self.Session() as s:
                post = (await s.execute(select(SocialPost))).scalar_one()
                self.assertEqual(post.owner_email, OWNER)
                self.assertEqual(post.video_path, os.path.join(os.path.abspath(self.upload_dir), upload_id))

            # A caller-chosen path is not an accepted field, and a forged id fails.
            res = await self._create_post(client, "/etc/passwd")
            self.assertEqual(res.status_code, 400)
            res = await self._create_post(client, "0" * 32 + ".mp4")
            self.assertEqual(res.status_code, 400)
            self.assertIn("Upload not found", res.json()["detail"])

        self.run_async(run)

    def test_post_validation(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            res = await self._create_post(client, upload_id, platforms=["myspace"])
            self.assertEqual(res.status_code, 422)
            res = await self._create_post(client, upload_id, platforms=[])
            self.assertEqual(res.status_code, 422)
            past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            res = await self._create_post(client, upload_id, scheduled_at=past)
            self.assertEqual(res.status_code, 400)

        self.run_async(run)

    def test_posts_are_scoped_to_the_caller(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            mine = (await self._create_post(client, upload_id)).json()

            self.current_email = OTHER
            self.assertEqual((await client.get("/api/v1/social-scheduler/posts")).json(), [])
            self.assertEqual((await client.get(f"/api/v1/social-scheduler/posts/{mine['id']}")).status_code, 404)
            self.assertEqual(
                (await client.patch(f"/api/v1/social-scheduler/posts/{mine['id']}", json={"title": "pwned"})).status_code,
                404,
            )
            self.assertEqual((await client.delete(f"/api/v1/social-scheduler/posts/{mine['id']}")).status_code, 404)

            self.current_email = OWNER
            listed = (await client.get("/api/v1/social-scheduler/posts")).json()
            self.assertEqual([p["id"] for p in listed], [mine["id"]])
            self.assertEqual(listed[0]["title"], "Launch teaser")

        self.run_async(run)

    def test_edit_cancel_and_requeue_lifecycle(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            post = (await self._create_post(client, upload_id)).json()
            url = f"/api/v1/social-scheduler/posts/{post['id']}"

            res = await client.patch(url, json={"title": "Renamed", "platforms": ["instagram"]})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["title"], "Renamed")
            self.assertEqual(res.json()["platforms"], ["instagram"])

            # Client may not push the post into worker-owned states.
            self.assertEqual((await client.patch(url, json={"status": "posted"})).status_code, 422)

            res = await client.patch(url, json={"status": "cancelled"})
            self.assertEqual(res.json()["status"], "cancelled")

            # Simulate a failed publish attempt, then re-queue: outcomes are wiped.
            async with self.Session() as s:
                row = (await s.execute(select(SocialPost))).scalar_one()
                row.status = "failed"
                s.add(SocialPostResult(post_id=row.id, owner_email=OWNER, platform="instagram", status="failed", error="boom"))
                await s.commit()
            self.assertEqual(len((await client.get(url)).json()["results"]), 1)
            res = await client.patch(url, json={"status": "pending"})
            self.assertEqual(res.json()["status"], "pending")
            self.assertEqual(res.json()["results"], [])

            # A post mid-publish is locked.
            async with self.Session() as s:
                row = (await s.execute(select(SocialPost))).scalar_one()
                row.status = "posting"
                await s.commit()
            self.assertEqual((await client.patch(url, json={"title": "x"})).status_code, 409)
            self.assertEqual((await client.delete(url)).status_code, 409)

        self.run_async(run)

    def test_delete_removes_the_video_file(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            post = (await self._create_post(client, upload_id)).json()
            self.assertTrue(os.path.exists(os.path.join(self.upload_dir, upload_id)))
            res = await client.delete(f"/api/v1/social-scheduler/posts/{post['id']}")
            self.assertEqual(res.status_code, 200)
            self.assertFalse(os.path.exists(os.path.join(self.upload_dir, upload_id)))
            self.assertEqual((await client.get("/api/v1/social-scheduler/posts")).json(), [])

        self.run_async(run)

    # ── platforms / OAuth ────────────────────────────────────────────────────

    def test_platform_list_reports_configuration_and_connection_per_user(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/platforms")
            self.assertEqual(res.status_code, 200)
            by_name = {p["platform"]: p for p in res.json()}
            self.assertEqual(set(by_name), {"youtube", "instagram", "tiktok", "facebook"})
            self.assertTrue(by_name["youtube"]["configured"])
            self.assertFalse(by_name["instagram"]["configured"])
            self.assertFalse(by_name["facebook"]["configured"])
            self.assertFalse(any(p["connected"] for p in by_name.values()))

            async with self.Session() as s:
                s.add(SocialPlatformConnection(
                    owner_email=OTHER, platform="youtube", account_name="Other Channel",
                    encrypted_access_token=encrypt_credential("tok"),
                ))
                await s.commit()
            by_name = {p["platform"]: p for p in (await client.get("/api/v1/social-scheduler/platforms")).json()}
            self.assertFalse(by_name["youtube"]["connected"], "another user's connection must not show")

            self.current_email = OTHER
            by_name = {p["platform"]: p for p in (await client.get("/api/v1/social-scheduler/platforms")).json()}
            self.assertTrue(by_name["youtube"]["connected"])
            self.assertEqual(by_name["youtube"]["account_name"], "Other Channel")
            for key in ("access_token", "encrypted_access_token", "refresh_token"):
                self.assertNotIn(key, by_name["youtube"])

        self.run_async(run)

    def test_auth_url_requires_configuration_and_carries_a_signed_state(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/platforms/instagram/auth-url")
            self.assertEqual(res.status_code, 503)
            res = await client.get("/api/v1/social-scheduler/platforms/myspace/auth-url")
            self.assertEqual(res.status_code, 404)

            res = await client.get("/api/v1/social-scheduler/platforms/tiktok/auth-url")
            self.assertEqual(res.status_code, 200, res.text)
            auth_url = res.json()["auth_url"]
            self.assertTrue(auth_url.startswith("https://www.tiktok.com/v2/auth/authorize/?"))
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(auth_url).query)
            self.assertEqual(qs["client_key"], ["tt-key"])
            self.assertEqual(
                qs["redirect_uri"],
                ["https://api.example.com/api/v1/social-scheduler/platforms/tiktok/callback"],
            )
            payload = jwt.decode(qs["state"][0], "test-secret", algorithms=["HS256"])
            self.assertEqual(payload["sub"], OWNER)
            self.assertEqual(payload["platform"], "tiktok")
            self.assertEqual(payload["token_type"], "social_oauth_state")

        self.run_async(run)

    def test_youtube_auth_url_carries_pkce_verifier_and_callback_reuses_it(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/platforms/youtube/auth-url")
            self.assertEqual(res.status_code, 200, res.text)
            auth_url = res.json()["auth_url"]
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(auth_url).query)
            self.assertEqual(
                qs["redirect_uri"],
                ["http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback"],
            )
            state_param = qs["state"][0]
            payload = jwt.decode(state_param, "test-secret", algorithms=["HS256"])
            verifier = payload.get("code_verifier")
            self.assertTrue(is_valid_code_verifier(verifier), "state must carry a PKCE code verifier")
            self.assertEqual(payload["sub"], OWNER)
            self.assertEqual(payload["platform"], "youtube")
            self.assertEqual(payload["token_type"], "social_oauth_state")
            # The authorization URL's S256 challenge must be derived from the
            # verifier signed into the state (this is what Google validates at
            # token-exchange time).
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            )
            self.assertEqual(qs["code_challenge"], [expected])
            self.assertEqual(qs["code_challenge_method"], ["S256"])

            fake_tokens = {"access_token": "at-yt", "refresh_token": "rt-yt", "expires_in": 3600}
            fake_info = {"account_id": "UC123", "account_name": "Channel", "extra_data": {}}
            with patch.object(_youtube_cls(), "exchange_code", AsyncMock(return_value=fake_tokens)) as exchange, \
                 patch.object(_youtube_cls(), "get_account_info", AsyncMock(return_value=fake_info)):
                # Callback is a bare browser redirect: make the user dependency
                # unusable to prove identity still comes from the signed state.
                self.current_email = "nobody@test.dev"
                res = await client.get(
                    "/api/v1/social-scheduler/platforms/youtube/callback",
                    params={"code": "yt-code", "state": state_param},
                    follow_redirects=False,
                )
            self.assertEqual(res.status_code, 302, res.text)
            self.assertEqual(
                res.headers["location"],
                "http://localhost:5173/app/social-scheduler/settings?platform=youtube&connected=1",
            )
            # The exact verifier from the signed state must reach the token
            # exchange — never a fresh auto-generated one (the old bug).
            self.assertEqual(exchange.call_args.args, ("yt-code",))
            self.assertEqual(exchange.call_args.kwargs.get("code_verifier"), verifier)
            self.assertNotIn(fake_tokens["access_token"], res.headers["location"])

        self.run_async(run)

    def test_callback_stores_encrypted_tokens_for_the_state_owner(self):
        async def run(client):
            state = module._mint_oauth_state(OWNER, "tiktok")
            fake_tokens = {"access_token": "at-123", "refresh_token": "rt-456", "expires_in": 3600}
            fake_info = {"account_id": "open-id", "account_name": "@creator", "extra_data": {"union_id": "u1"}}
            with patch.object(module.TikTokService if hasattr(module, "TikTokService") else _tiktok_cls(), "exchange_code", AsyncMock(return_value=fake_tokens)), \
                 patch.object(_tiktok_cls(), "get_account_info", AsyncMock(return_value=fake_info)):
                # Callback is a browser redirect: no auth header. Make the
                # user dependency unusable to prove it is not consulted.
                self.current_email = "nobody@test.dev"
                res = await client.get(
                    "/api/v1/social-scheduler/platforms/tiktok/callback",
                    params={"code": "abc", "state": state},
                    follow_redirects=False,
                )
            self.assertEqual(res.status_code, 302, res.text)
            self.assertEqual(
                res.headers["location"],
                "http://localhost:5173/app/social-scheduler/settings?platform=tiktok&connected=1",
            )

            async with self.Session() as s:
                conn = (await s.execute(select(SocialPlatformConnection))).scalar_one()
                self.assertEqual(conn.owner_email, OWNER)
                self.assertEqual(conn.platform, "tiktok")
                self.assertEqual(conn.account_name, "@creator")
                self.assertEqual(conn.extra_data, {"union_id": "u1"})
                self.assertNotIn("at-123", conn.encrypted_access_token)
                self.assertEqual(decrypt_credential(conn.encrypted_access_token), "at-123")
                self.assertEqual(decrypt_credential(conn.encrypted_refresh_token), "rt-456")
                self.assertIsNotNone(conn.expires_at)

            # Reconnecting the same platform replaces the tokens instead of 500ing on the unique index.
            state2 = module._mint_oauth_state(OWNER, "tiktok")
            with patch.object(_tiktok_cls(), "exchange_code", AsyncMock(return_value={**fake_tokens, "access_token": "at-999"})), \
                 patch.object(_tiktok_cls(), "get_account_info", AsyncMock(return_value=fake_info)):
                res = await client.get(
                    "/api/v1/social-scheduler/platforms/tiktok/callback",
                    params={"code": "def", "state": state2},
                    follow_redirects=False,
                )
            self.assertEqual(res.status_code, 302)
            async with self.Session() as s:
                conns = (await s.execute(select(SocialPlatformConnection))).scalars().all()
                self.assertEqual(len(conns), 1)
                self.assertEqual(decrypt_credential(conns[0].encrypted_access_token), "at-999")

        self.run_async(run)

    def test_callback_rejects_bad_state_and_provider_errors_without_storing(self):
        async def run(client):
            base = "/api/v1/social-scheduler/platforms/youtube/callback"
            cases = {
                "missing": {"code": "abc"},
                "garbage": {"code": "abc", "state": "not-a-jwt"},
                "wrong platform": {"code": "abc", "state": module._mint_oauth_state(OWNER, "tiktok")},
                "wrong secret": {
                    "code": "abc",
                    "state": jwt.encode({"sub": OWNER, "platform": "youtube", "token_type": "social_oauth_state"}, "other", algorithm="HS256"),
                },
                "expired": {
                    "code": "abc",
                    "state": jwt.encode(
                        {"sub": OWNER, "platform": "youtube", "token_type": "social_oauth_state",
                         "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                        "test-secret", algorithm="HS256",
                    ),
                },
            }
            with patch.object(_youtube_cls(), "exchange_code", AsyncMock(side_effect=AssertionError("must not exchange"))):
                for label, params in cases.items():
                    res = await client.get(base, params=params, follow_redirects=False)
                    self.assertEqual(res.status_code, 302, label)
                    self.assertIn("error=", res.headers["location"], label)
                res = await client.get(base, params={"error": "access_denied", "error_description": "User cancelled"}, follow_redirects=False)
                self.assertIn("error=User+cancelled", res.headers["location"])
            async with self.Session() as s:
                self.assertEqual((await s.execute(select(SocialPlatformConnection))).scalars().all(), [])

        self.run_async(run)

    def test_disconnect_only_removes_the_callers_connection(self):
        async def run(client):
            async with self.Session() as s:
                for who in (OWNER, OTHER):
                    s.add(SocialPlatformConnection(owner_email=who, platform="youtube", encrypted_access_token=encrypt_credential("t")))
                await s.commit()
            res = await client.delete("/api/v1/social-scheduler/platforms/youtube")
            self.assertEqual(res.status_code, 200)
            self.assertEqual((await client.delete("/api/v1/social-scheduler/platforms/youtube")).status_code, 404)
            async with self.Session() as s:
                remaining = (await s.execute(select(SocialPlatformConnection))).scalars().all()
                self.assertEqual([c.owner_email for c in remaining], [OTHER])

        self.run_async(run)

    # ── stats / calendar ─────────────────────────────────────────────────────

    def test_stats_and_calendar_aggregate_only_the_callers_posts(self):
        async def run(client):
            upload_id = (await self._upload(client)).json()["upload_id"]
            soon = datetime.now(timezone.utc) + timedelta(days=2)
            far = datetime.now(timezone.utc) + timedelta(days=40)
            (await self._create_post(client, upload_id, scheduled_at=soon.isoformat())).json()
            (await self._create_post(client, upload_id, scheduled_at=far.isoformat())).json()
            async with self.Session() as s:
                s.add(SocialPost(
                    owner_email=OTHER, title="theirs", caption="", video_path="/x", video_url="/x",
                    platforms=["youtube"], scheduled_at=soon,
                ))
                s.add(SocialPost(
                    owner_email=OWNER, title="old success", caption="", video_path="/x", video_url="/x",
                    platforms=["youtube"], scheduled_at=soon - timedelta(days=10), status="posted",
                ))
                await s.commit()
                done = (await s.execute(select(SocialPost).where(SocialPost.title == "old success"))).scalar_one()
                s.add(SocialPostResult(post_id=done.id, owner_email=OWNER, platform="youtube", status="posted"))
                s.add(SocialPlatformConnection(owner_email=OWNER, platform="tiktok", encrypted_access_token=encrypt_credential("t")))
                await s.commit()

            stats = (await client.get("/api/v1/social-scheduler/stats")).json()
            self.assertEqual(stats["scheduled_this_week"], 1)
            self.assertEqual(stats["total_scheduled"], 2)
            self.assertEqual(stats["total_published"], 1)
            self.assertEqual(stats["total_failed"], 0)
            self.assertEqual(stats["connected_platforms"], ["tiktok"])
            self.assertEqual(stats["per_platform"]["youtube"], {"posted": 1, "failed": 0})
            self.assertTrue(stats["next_post_in"].startswith("in 1 day"), stats["next_post_in"])

            month = soon.strftime("%Y-%m")
            cal = (await client.get("/api/v1/social-scheduler/calendar", params={"month": month})).json()
            titles = [p["title"] for day in cal for p in day["posts"]]
            self.assertIn("Launch teaser", titles)
            self.assertNotIn("theirs", titles)
            self.assertTrue(all(day["date"].startswith(month) for day in cal))
            self.assertEqual((await client.get("/api/v1/social-scheduler/calendar", params={"month": "2026-13"})).status_code, 400)

        self.run_async(run)


def _tiktok_cls():
    from services.social.tiktok import TikTokService

    return TikTokService


def _youtube_cls():
    from services.social.youtube import YouTubeService

    return YouTubeService


if __name__ == "__main__":
    unittest.main()

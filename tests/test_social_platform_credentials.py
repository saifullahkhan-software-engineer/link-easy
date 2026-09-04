"""Operator-set platform app credentials (DB row overrides env).

Covers services/social/credentials.py + the /platforms/credentials routes:

  * GET    /api/v1/social-scheduler/platforms/credentials
  * PUT    /api/v1/social-scheduler/platforms/credentials/{platform}
  * DELETE /api/v1/social-scheduler/platforms/credentials/{platform}

and their effect on the existing OAuth surface:

  * GET /platforms reports a DB-configured platform as configured for every
    user (not just the operator who saved it);
  * the auth-url gate opens for a platform whose env pair is empty once a DB
    row exists, and the URL carries the DB values when both exist;
  * the OAuth callback exchanges codes with the DB app id/secret;
  * secrets are write-only — no endpoint ever returns them;
  * writes are admin-gated once ADMIN_API_ENFORCED=true;
  * the sync worker variant applies the same effective credentials.
"""
import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
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
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1 import social_scheduler as module  # noqa: E402
from api.v1.social_scheduler import router  # noqa: E402
from core.config import settings  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.social_scheduler import PlatformCredential  # noqa: E402
from models.user import User  # noqa: E402
from services.social.credentials import apply_credentials_sync  # noqa: E402
from services.social.facebook import FacebookService  # noqa: E402
from services.social.instagram import InstagramService  # noqa: E402
from services.social.tiktok import TikTokService  # noqa: E402
from services.social.youtube import YouTubeService  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

OWNER = "owner@test.dev"  # role=admin (legacy column → is_admin fallback)
OTHER = "other@test.dev"  # role=customer

SERVICE_CLASSES = {
    "youtube": YouTubeService,
    "instagram": InstagramService,
    "facebook": FacebookService,
    "tiktok": TikTokService,
}


def _user(email, role="customer"):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role=role)


class PlatformCredentialsTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        # Env is configured for youtube + tiktok only; instagram + facebook
        # start unconfigured so DB rows can be exercised against both states.
        self._settings_patch = patch.multiple(
            settings,
            YOUTUBE_CLIENT_ID="yt-id",
            YOUTUBE_CLIENT_SECRET="yt-secret",
            YOUTUBE_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/youtube/callback",
            INSTAGRAM_APP_ID="",
            INSTAGRAM_APP_SECRET="",
            TIKTOK_CLIENT_KEY="tt-key",
            TIKTOK_CLIENT_SECRET="tt-secret",
            TIKTOK_REDIRECT_URI="http://localhost:8000/api/v1/social-scheduler/platforms/tiktok/callback",
            FACEBOOK_APP_ID="",
            FACEBOOK_APP_SECRET="",
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
                return (
                    await session.execute(select(User).where(User.email == self.current_email))
                ).scalar_one()

        app.dependency_overrides[get_db] = override_get_db
        app.dependency_overrides[get_current_user] = override_user
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        self._settings_patch.stop()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with self.Session() as s:
            s.add_all([_user(OWNER, role="admin"), _user(OTHER)])
            await s.commit()

    def run_async(self, fn):
        async def runner():
            async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                return await fn(client)

        return self.loop.run_until_complete(runner())

    async def _put(self, client, platform, **fields):
        return await client.put(f"/api/v1/social-scheduler/platforms/credentials/{platform}", json=fields)

    async def _get_credentials(self, client):
        res = await client.get("/api/v1/social-scheduler/platforms/credentials")
        self.assertEqual(res.status_code, 200, res.text)
        return {p["platform"]: p for p in res.json()}

    # ── reporting ────────────────────────────────────────────────────────────

    def test_list_reports_environment_configured_platforms(self):
        async def run(client):
            by_name = await self._get_credentials(client)
            self.assertEqual(set(by_name), {"youtube", "instagram", "tiktok", "facebook"})
            self.assertTrue(by_name["youtube"]["configured"])
            self.assertEqual(by_name["youtube"]["source"], "environment")
            self.assertEqual(by_name["youtube"]["identifier"], "")
            self.assertTrue(by_name["tiktok"]["configured"])
            self.assertFalse(by_name["instagram"]["configured"])
            self.assertFalse(by_name["facebook"]["configured"])
            # Values are never echoed; only booleans/metadata are reported.
            for p in by_name.values():
                self.assertEqual(set(p), {"platform", "label", "configured", "source", "identifier", "has_secret", "updated_at"})
                self.assertFalse(p["has_secret"])

        self.run_async(run)

    def test_save_credentials_sets_database_source_and_identifier(self):
        async def run(client):
            res = await self._put(client, "instagram", app_id="ig-app-db", app_secret="ig-secret-db")
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("saved", res.json()["message"])

            by_name = await self._get_credentials(client)
            self.assertTrue(by_name["instagram"]["configured"])
            self.assertEqual(by_name["instagram"]["source"], "database")
            self.assertEqual(by_name["instagram"]["identifier"], "ig-app-db")
            self.assertTrue(by_name["instagram"]["has_secret"])

            async with self.Session() as s:
                row = (
                    await s.execute(select(PlatformCredential).where(PlatformCredential.platform == "instagram"))
                ).scalar_one()
                self.assertEqual(row.client_id, "ig-app-db")
                self.assertEqual(row.client_secret, "ig-secret-db")

        self.run_async(run)

    def test_db_configured_platform_is_visible_to_regular_users(self):
        async def run(client):
            await self._put(client, "facebook", app_id="fb-app-db", app_secret="fb-secret-db")
            # A customer (not the operator) sees facebook as connectable now.
            self.current_email = OTHER
            res = await client.get("/api/v1/social-scheduler/platforms")
            self.assertEqual(res.status_code, 200, res.text)
            by_name = {p["platform"]: p for p in res.json()}
            self.assertTrue(by_name["facebook"]["configured"])
            self.assertFalse(by_name["facebook"]["connected"])

        self.run_async(run)

    def test_secrets_are_never_returned_by_any_endpoint(self):
        async def run(client):
            await self._put(client, "facebook", app_id="fb-app-db", app_secret="super-secret-value")
            creds = (await self._get_credentials(client))["facebook"]
            self.assertEqual(creds["identifier"], "fb-app-db")
            self.assertEqual(creds["has_secret"], True)
            raw = (await client.get("/api/v1/social-scheduler/platforms/credentials")).text
            self.assertNotIn("super-secret-value", raw)
            # The identifier is deliberately echoed; the secret never is.
            self.assertIn("fb-app-db", raw)

            platforms_raw = (await client.get("/api/v1/social-scheduler/platforms")).text
            self.assertNotIn("super-secret-value", platforms_raw)
            # Regular users see facebook as configured — but no identifier.
            self.assertNotIn("fb-app-db", platforms_raw)

        self.run_async(run)

    # ── validation ───────────────────────────────────────────────────────────

    def test_put_requires_both_fields_of_the_platforms_pair(self):
        async def run(client):
            # Instagram needs app_id + app_secret.
            self.assertEqual((await self._put(client, "instagram", app_id="x")).status_code, 400)
            self.assertEqual((await self._put(client, "instagram", app_id="x", app_secret=" ")).status_code, 400)
            # TikTok's pair is client_key/client_secret — app_id is not accepted.
            self.assertEqual(
                (await self._put(client, "tiktok", app_id="x", app_secret="y")).status_code, 400
            )
            # Facebook pair mirrors Instagram's.
            self.assertEqual(
                (await self._put(client, "facebook", app_id="x", app_secret="y")).status_code, 200
            )

        self.run_async(run)

    def test_put_rejects_unknown_platform_and_unknown_fields(self):
        async def run(client):
            res = await client.put(
                "/api/v1/social-scheduler/platforms/credentials/myspace",
                json={"app_id": "x", "app_secret": "y"},
            )
            self.assertEqual(res.status_code, 404)
            # extra="forbid": a typo'd field must not be silently dropped.
            res = await self._put(client, "instagram", app_id="x", app_secret="y", bogus="z")
            self.assertEqual(res.status_code, 422)

        self.run_async(run)

    def test_put_replaces_the_existing_row(self):
        async def run(client):
            await self._put(client, "youtube", client_id="db-1", client_secret="s1")
            await self._put(client, "youtube", client_id="db-2", client_secret="s2")
            by_name = await self._get_credentials(client)
            self.assertEqual(by_name["youtube"]["identifier"], "db-2")
            async with self.Session() as s:
                rows = (await s.execute(select(PlatformCredential))).scalars().all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].client_id, "db-2")
                self.assertEqual(rows[0].client_secret, "s2")

        self.run_async(run)

    def test_delete_falls_back_to_environment(self):
        async def run(client):
            await self._put(client, "youtube", client_id="db-yt", client_secret="db-secret")
            self.assertEqual((await self._get_credentials(client))["youtube"]["source"], "database")

            res = await client.delete("/api/v1/social-scheduler/platforms/credentials/youtube")
            self.assertEqual(res.status_code, 200, res.text)
            self.assertIn("removed", res.json()["message"])

            by_name = await self._get_credentials(client)
            self.assertEqual(by_name["youtube"]["source"], "environment")
            self.assertEqual(by_name["youtube"]["identifier"], "")
            # The platform stays connectable via the environment pair.
            self.assertTrue(by_name["youtube"]["configured"])

            # Removing again has nothing to remove.
            res = await client.delete("/api/v1/social-scheduler/platforms/credentials/youtube")
            self.assertEqual(res.status_code, 404)

        self.run_async(run)

    # ── effect on OAuth ──────────────────────────────────────────────────────

    def test_db_credentials_open_the_auth_url_for_an_env_unconfigured_platform(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/platforms/instagram/auth-url")
            self.assertEqual(res.status_code, 503)

            await self._put(client, "instagram", app_id="ig-app-db", app_secret="ig-secret-db")
            res = await client.get("/api/v1/social-scheduler/platforms/instagram/auth-url")
            self.assertEqual(res.status_code, 200, res.text)
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(res.json()["auth_url"]).query)
            self.assertEqual(qs["client_id"], ["ig-app-db"])

        self.run_async(run)

    def test_auth_url_prefers_db_credentials_over_environment(self):
        async def run(client):
            # tiktok is env-configured (tt-key) — a DB row must win.
            await self._put(client, "tiktok", client_key="db-tt-key", client_secret="db-tt-secret")
            res = await client.get("/api/v1/social-scheduler/platforms/tiktok/auth-url")
            self.assertEqual(res.status_code, 200, res.text)
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(res.json()["auth_url"]).query)
            self.assertEqual(qs["client_key"], ["db-tt-key"])

        self.run_async(run)

    def test_oauth_callback_exchanges_with_db_credentials(self):
        async def run(client):
            await self._put(client, "instagram", app_id="ig-app-db", app_secret="ig-secret-db")
            state = module._mint_oauth_state(OWNER, "instagram")
            seen = {}

            async def fake_exchange(self, code, *, code_verifier=None):
                seen["app_id"] = self.app_id
                seen["app_secret"] = self.app_secret
                return {"access_token": "at-db", "refresh_token": None, "expires_in": 3600}

            async def fake_info(self, access_token):
                return {"account_id": "ig-1", "account_name": "@db", "extra_data": {}}

            with patch.object(InstagramService, "exchange_code", fake_exchange), \
                 patch.object(InstagramService, "get_account_info", fake_info):
                res = await client.get(
                    "/api/v1/social-scheduler/platforms/instagram/callback",
                    params={"code": "db-code", "state": state},
                    follow_redirects=False,
                )
            self.assertEqual(res.status_code, 302, res.text)
            self.assertEqual(seen["app_id"], "ig-app-db")
            self.assertEqual(seen["app_secret"], "ig-secret-db")

        self.run_async(run)

    # ── admin gating ─────────────────────────────────────────────────────────

    def test_credentials_endpoints_are_admin_only_when_enforced(self):
        async def run(client):
            self.current_email = OTHER
            self.assertEqual(
                (await client.get("/api/v1/social-scheduler/platforms/credentials")).status_code, 200,
                "bootstrap mode (not enforced) lets a non-admin through — like every admin surface",
            )
            with patch.object(settings, "ADMIN_API_ENFORCED", True):
                res = await client.get("/api/v1/social-scheduler/platforms/credentials")
                self.assertEqual(res.status_code, 403)
                res = await self._put(client, "facebook", app_id="x", app_secret="y")
                self.assertEqual(res.status_code, 403)
                res = await client.delete("/api/v1/social-scheduler/platforms/credentials/facebook")
                self.assertEqual(res.status_code, 403)

                self.current_email = OWNER  # admin
                res = await client.get("/api/v1/social-scheduler/platforms/credentials")
                self.assertEqual(res.status_code, 200)
                res = await self._put(client, "facebook", app_id="x", app_secret="y")
                self.assertEqual(res.status_code, 200)

        self.run_async(run)

    # ── worker (sync) variant ────────────────────────────────────────────────

    def test_sync_variant_applies_db_override_and_env_fallback(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            sync_engine = create_engine(f"sqlite:///{path}")
            Base.metadata.create_all(sync_engine)
            SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)
            with SyncSession() as db:
                db.add(PlatformCredential(platform="instagram", client_id="ig-sync", client_secret="ig-secret-sync"))
                db.commit()

                svc = SimpleNamespace(app_id="", app_secret="")
                apply_credentials_sync(db, "instagram", svc)
                self.assertEqual(svc.app_id, "ig-sync")
                self.assertEqual(svc.app_secret, "ig-secret-sync")

                # No DB row for youtube → the environment pair applies.
                svc = SimpleNamespace(client_id="", client_secret="")
                apply_credentials_sync(db, "youtube", svc)
                self.assertEqual(svc.client_id, "yt-id")
                self.assertEqual(svc.client_secret, "yt-secret")
        finally:
            sync_engine.dispose()
            if os.path.exists(path):
                os.remove(path)

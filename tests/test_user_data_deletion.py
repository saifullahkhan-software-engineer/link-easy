"""Account deletion flow for Meta's User Data Deletion requirement.

Covers api/v1/user_data.py end to end against the real router on an
in-memory database:

  * POST /deletion-request  — generic response (no account enumeration), a
    one-time signed link is emailed only when the account exists;
  * POST /deletion-confirm  — the signed one-time token deletes the user and
    every row they own (campaigns, feed jobs, social posts, WhatsApp, roles)
    and clears one-time tokens (password resets + deletion links);
  * invalid / expired / replayed / wrong-type tokens delete nothing;
  * deletion is never triggered by a bare email — the token is mandatory.
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("DATA_DELETION_URL", "http://localhost/delete-confirm")
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

from api.dependencies import get_db  # noqa: E402
from api.v1.user_data import router  # noqa: E402
from core.config import settings  # noqa: E402
from core.security import create_account_deletion_token  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.campaign import Campaign, CampaignStatus, CampaignStep, CampaignStepType  # noqa: E402
from models.campaign_job import CampaignJob, JobStatus  # noqa: E402
from models.feed_lead import FeedLead  # noqa: E402
from models.feed_scroll_applied_post import FeedScrollAppliedPost  # noqa: E402
from models.feed_scroll_job import FeedScrollJob  # noqa: E402
from models.feed_scroll_result import FeedScrollResult  # noqa: E402
from models.lead import Lead, LeadStatus  # noqa: E402
from models.linkedin_account import LinkedInAccount  # noqa: E402
from models.rbac import Role, UserRoleLink  # noqa: E402
from models.social_scheduler import (  # noqa: E402
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
)
from models.user import PasswordResetToken, User, UserDeletionToken  # noqa: E402
from models.whatsapp import (  # noqa: E402
    WhatsAppRawMessage,
    WhatsAppScanFilter,
    WhatsAppSession,
)

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

EMAIL = "target@test.dev"
OTHER = "other@test.dev"
GENERIC_REQUEST_MESSAGE = (
    "If an account exists for this email, a confirmation link with "
    "instructions is on its way. Check your inbox (and spam folder)."
)


def _user(email, role="customer"):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role=role)


def _future_token_row(token_id, email, minutes=30):
    return UserDeletionToken(
        token_id=token_id,
        email=email,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
    )


class UserDataDeletionTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self._settings_patch = patch.multiple(
            settings,
            DATA_DELETION_URL="http://localhost/delete-confirm",
            RATE_LIMIT_ENABLED=False,
        )
        self._settings_patch.start()

        app = FastAPI()
        app.include_router(router)

        async def override_get_db():
            async with self.Session() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        self._settings_patch.stop()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    def run_async(self, fn):
        async def runner():
            async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as client:
                return await fn(client)

        return self.loop.run_until_complete(runner())

    async def _seed_user(self, email=EMAIL, **extra):
        async with self.Session() as s:
            s.add(_user(email, **extra))
            await s.commit()

    async def _counts(self, *models_):
        async with self.Session() as s:
            return {m.__tablename__: len((await s.execute(select(m))).scalars().all()) for m in models_}

    # ── request ──────────────────────────────────────────────────────────────

    def test_request_is_generic_when_account_missing(self):
        async def run(client):
            send = AsyncMock()
            with patch("api.v1.user_data.send_account_deletion_email", send):
                res = await client.post(
                    "/api/v1/user-data/deletion-request", json={"email": "nobody@test.dev"}
                )
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["message"], GENERIC_REQUEST_MESSAGE)
            send.assert_not_awaited()
            async with self.Session() as s:
                rows = (await s.execute(select(UserDeletionToken))).scalars().all()
                self.assertEqual(rows, [])

        self.run_async(run)

    def test_request_emails_a_one_time_signed_link_for_existing_user(self):
        async def run(client):
            await self._seed_user()
            send = AsyncMock()
            with patch("api.v1.user_data.send_account_deletion_email", send):
                res = await client.post(
                    "/api/v1/user-data/deletion-request", json={"email": EMAIL.upper()}
                )
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["message"], GENERIC_REQUEST_MESSAGE)
            send.assert_awaited_once()
            email_arg, link = send.call_args.args
            self.assertEqual(email_arg, EMAIL)
            self.assertTrue(link.startswith("http://localhost/delete-confirm?token="))
            token = link.split("token=", 1)[1]
            payload = jwt.decode(token, "test-secret", algorithms=["HS256"])
            self.assertEqual(payload["sub"], EMAIL)
            self.assertEqual(payload["token_type"], "account_deletion")

            async with self.Session() as s:
                rows = (await s.execute(select(UserDeletionToken))).scalars().all()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].token_id, payload["jti"])
                self.assertEqual(rows[0].email, EMAIL)

        self.run_async(run)

    def test_request_never_reveals_whether_an_account_exists(self):
        async def run(client):
            await self._seed_user()
            send = AsyncMock()
            with patch("api.v1.user_data.send_account_deletion_email", send):
                existing = await client.post(
                    "/api/v1/user-data/deletion-request", json={"email": EMAIL}
                )
                missing = await client.post(
                    "/api/v1/user-data/deletion-request", json={"email": "absent@test.dev"}
                )
            self.assertEqual(existing.json(), {"message": GENERIC_REQUEST_MESSAGE})
            self.assertEqual(missing.json(), {"message": GENERIC_REQUEST_MESSAGE})
            self.assertEqual(existing.json(), missing.json())

        self.run_async(run)

    # ── confirm: validation ──────────────────────────────────────────────────

    def test_confirm_rejects_invalid_wrong_type_and_expired_tokens(self):
        async def run(client):
            await self._seed_user()
            now = datetime.now(timezone.utc)
            async with self.Session() as s:
                s.add(_future_token_row("good-token", EMAIL))
                s.add(_future_token_row("expired-token", EMAIL, minutes=-5))
                await s.commit()
            good = create_account_deletion_token(EMAIL, "good-token")

            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": "garbage"})
            self.assertEqual(res.status_code, 400)

            # A password-reset token is not an account-deletion token.
            reset = jwt.encode(
                {"sub": EMAIL, "jti": "x", "exp": now + timedelta(minutes=30), "token_type": "password_reset"},
                "test-secret",
                algorithm="HS256",
            )
            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": reset})
            self.assertEqual(res.status_code, 400)

            # Row expired even though the JWT is still structurally valid.
            expired = create_account_deletion_token(EMAIL, "expired-token")
            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": expired})
            self.assertEqual(res.status_code, 400)

            # Nothing was deleted by the rejected attempts.
            async with self.Session() as s:
                user = (await s.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
                self.assertIsNotNone(user)
                rows = (await s.execute(select(UserDeletionToken))).scalars().all()
                self.assertEqual(len(rows), 2)

        self.run_async(run)

    def test_confirm_without_token_is_impossible(self):
        async def run(client):
            await self._seed_user()
            # A bare email is never enough — the schema only accepts a token.
            res = await client.post(
                "/api/v1/user-data/deletion-confirm", json={"email": EMAIL}
            )
            self.assertEqual(res.status_code, 422)
            res = await client.post("/api/v1/user-data/deletion-confirm", json={})
            self.assertEqual(res.status_code, 422)
            async with self.Session() as s:
                user = (await s.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
                self.assertIsNotNone(user)

        self.run_async(run)

    # ── confirm: deletion ────────────────────────────────────────────────────

    def test_confirm_deletes_user_and_every_owned_row(self):
        async def run(client):
            now = datetime.now(timezone.utc)
            async with self.Session() as s:
                s.add(_user(EMAIL))
                s.add(_user(OTHER))
                s.add(Role(id=1, name="admin"))
                s.add(UserRoleLink(user_email=EMAIL, role_id=1, granted_by="seed"))
                account = LinkedInAccount(
                    owner_email=EMAIL,
                    linkedin_email="li@target.dev",
                    encrypted_password="enc",
                    profile_dir="/profiles/li",
                    status="active",
                )
                s.add(account)
                campaign = Campaign(id="camp-1", account_email="li@target.dev", name="C", status=CampaignStatus.ACTIVE)
                s.add(campaign)
                s.add(CampaignStep(id="step-1", campaign_id="camp-1", step_order=1, step_type=CampaignStepType.VISIT_PROFILE, delay_hours=0))
                lead = Lead(id="lead-1", campaign_id="camp-1", linkedin_url="https://li/in/x", status=LeadStatus.PENDING)
                s.add(lead)
                s.add(CampaignJob(id="job-1", campaign_id="camp-1", lead_id="lead-1", step_type="visit_profile", status=JobStatus.DONE))
                feed = FeedScrollJob(id="feed-1", account_email="li@target.dev", owner_email=EMAIL, name="F")
                s.add(feed)
                s.add(FeedScrollResult(id="fr-1", feed_scroll_job_id="feed-1", scan_batch_id="b1", scanned_at=now))
                s.add(FeedLead(id="fl-1", owner_email=EMAIL, feed_scroll_job_id="feed-1", linkedin_url="https://li/in/y"))
                s.add(FeedScrollAppliedPost(id="ap-1", feed_scroll_job_id="feed-1", owner_email=EMAIL, post_url="https://li/p/1", author_profile_url="https://li/in/a"))
                post = SocialPost(
                    id="sp-1", owner_email=EMAIL, title="t", caption="c", video_path="/tmp/v.mp4",
                    video_url="http://localhost/v.mp4", platforms=["youtube"], scheduled_at=now + timedelta(days=1),
                )
                s.add(post)
                s.add(SocialPostResult(post_id="sp-1", owner_email=EMAIL, platform="youtube"))
                s.add(SocialPlatformConnection(owner_email=EMAIL, platform="youtube", encrypted_access_token="enc"))
                s.add(WhatsAppSession(owner_email=EMAIL, status="connected", is_active=True))
                filt = WhatsAppScanFilter(owner_email=EMAIL, name="Filter")
                s.add(filt)
                s.add(WhatsAppRawMessage(filter_id=1, group_id=7, sender_name="s"))
                s.add(PasswordResetToken(token_id="prt", email=EMAIL, expires_at=now + timedelta(minutes=30)))
                s.add(_future_token_row("del-tok", EMAIL))
                await s.commit()

            token = create_account_deletion_token(EMAIL, "del-tok")
            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": token})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["message"], "Your account and all associated data have been deleted.")

            async with self.Session() as s:
                for model in (
                    LinkedInAccount, Campaign, CampaignStep, Lead, CampaignJob,
                    FeedScrollJob, FeedScrollResult, FeedLead, FeedScrollAppliedPost,
                    SocialPost, SocialPostResult, SocialPlatformConnection,
                    WhatsAppSession, WhatsAppScanFilter, WhatsAppRawMessage,
                    UserRoleLink, PasswordResetToken, UserDeletionToken,
                ):
                    rows = (await s.execute(select(model))).scalars().all()
                    self.assertEqual(rows, [], f"{model.__tablename__} should be empty after deletion")
                # The deleted user is gone; the other user is untouched.
                self.assertIsNone(
                    (await s.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
                )
                self.assertIsNotNone(
                    (await s.execute(select(User).where(User.email == OTHER))).scalar_one_or_none()
                )

        self.run_async(run)

    def test_confirm_token_is_one_time(self):
        async def run(client):
            await self._seed_user()
            async with self.Session() as s:
                s.add(_future_token_row("one-time", EMAIL))
                await s.commit()
            token = create_account_deletion_token(EMAIL, "one-time")

            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": token})
            self.assertEqual(res.status_code, 200, res.text)
            # Replaying the same (now consumed) token deletes nothing more and
            # answers with the invalid-token message.
            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": token})
            self.assertEqual(res.status_code, 400)

        self.run_async(run)

    def test_confirm_consumes_outstanding_password_reset_and_deletion_tokens(self):
        async def run(client):
            await self._seed_user()
            now = datetime.now(timezone.utc)
            async with self.Session() as s:
                s.add(PasswordResetToken(token_id="pr-1", email=EMAIL, expires_at=now + timedelta(minutes=30)))
                s.add(PasswordResetToken(token_id="pr-2", email=OTHER, expires_at=now + timedelta(minutes=30)))
                s.add(_future_token_row("del-1", EMAIL))
                s.add(_future_token_row("del-2", OTHER))
                await s.commit()

            token = create_account_deletion_token(EMAIL, "del-1")
            res = await client.post("/api/v1/user-data/deletion-confirm", json={"token": token})
            self.assertEqual(res.status_code, 200, res.text)

            async with self.Session() as s:
                # The deleted user's one-time tokens are gone; OTHER's remain.
                prs = (await s.execute(select(PasswordResetToken))).scalars().all()
                self.assertEqual([r.token_id for r in prs], ["pr-2"])
                dels = (await s.execute(select(UserDeletionToken))).scalars().all()
                self.assertEqual([r.token_id for r in dels], ["del-2"])
                # The user is gone.
                self.assertIsNone(
                    (await s.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
                )

        self.run_async(run)


if __name__ == "__main__":
    unittest.main()

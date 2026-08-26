"""Tests for POST /api/v1/linkedin/account/cookie.

Cookie import lets a user connect LinkedIn by pasting a session cookie from
their own browser, so the server never drives the sign-in form that datacenter
IPs get CAPTCHA'd on. These tests cover the account row it creates, cleanup on
failure, and the relogin guard for accounts that have no stored password.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import api.v1.linkedin as linkedin_api  # noqa: E402
from automation.session import LinkedInSessionStatus  # noqa: E402
from core.config import settings  # noqa: E402
from database import Base  # noqa: E402
from models.linkedin_account import (  # noqa: E402
    AuthMethod,
    LinkedInAccount,
    LinkedInAccountStatus,
)
from models.user import User  # noqa: E402
from schemas.linkedin import LinkedInAccountCookieConnect  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64

LI_AT = "AQEDATExampleToken_-0123456789abcdefghijklmnopqrstuvwxyzABCDEF=="


class _FakeCtx:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _FakePw:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


def _resources():
    return (_FakePw(), None, _FakeCtx(), object(), "ua")


def _user(email="owner@test.dev"):
    return SimpleNamespace(email=email)


class CookieConnectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(
                User(
                    first_name="Owner",
                    last_name="User",
                    email="owner@test.dev",
                    hashed_password="x",
                    is_verified=True,
                    role="customer",
                )
            )
            await db.commit()

        self._patches = [
            patch.object(linkedin_api, "ensure_profile_dir", lambda account: account.profile_dir),
            patch.object(linkedin_api, "acquire_profile_lock", lambda *a, **k: object()),
            patch.object(linkedin_api, "release_profile_lock", lambda lock: None),
            patch.object(linkedin_api.shutil, "rmtree", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()

    async def asyncTearDown(self):
        for p in self._patches:
            p.stop()
        await self.engine.dispose()

    def _payload(self, cookie=LI_AT):
        return LinkedInAccountCookieConnect(
            linkedin_email="target@linkedin.dev",
            session_cookie=cookie,
            label="Cookie account",
        )

    async def test_successful_import_creates_cookie_account(self):
        with patch.object(
            linkedin_api,
            "linkedin_login_with_cookies",
            AsyncMock(return_value=(LinkedInSessionStatus.VALID, _resources(), None)),
        ):
            async with self.Session() as db:
                result = await linkedin_api.connect_linkedin_account_with_cookie(
                    payload=self._payload(), db=db, current_user=_user()
                )

        self.assertEqual(result.status, "LOGIN_SUCCESS")

        async with self.Session() as db:
            account = (await db.execute(select(LinkedInAccount))).scalars().first()

        self.assertIsNotNone(account)
        self.assertEqual(account.status, LinkedInAccountStatus.ACTIVE)
        # The whole point: no password is stored on this path.
        self.assertIsNone(account.encrypted_password)
        self.assertEqual(account.auth_method, AuthMethod.COOKIE.value)
        self.assertEqual(account.owner_email, "owner@test.dev")

    async def test_ownership_comes_from_the_authenticated_user(self):
        with patch.object(
            linkedin_api,
            "linkedin_login_with_cookies",
            AsyncMock(return_value=(LinkedInSessionStatus.VALID, _resources(), None)),
        ):
            async with self.Session() as db:
                await linkedin_api.connect_linkedin_account_with_cookie(
                    payload=self._payload(), db=db, current_user=_user("owner@test.dev")
                )
        async with self.Session() as db:
            account = (await db.execute(select(LinkedInAccount))).scalars().first()
        self.assertEqual(account.owner_email, "owner@test.dev")

    async def test_browser_is_closed_after_success(self):
        resources = _resources()
        with patch.object(
            linkedin_api,
            "linkedin_login_with_cookies",
            AsyncMock(return_value=(LinkedInSessionStatus.VALID, resources, None)),
        ):
            async with self.Session() as db:
                await linkedin_api.connect_linkedin_account_with_cookie(
                    payload=self._payload(), db=db, current_user=_user()
                )
        pw, _browser, ctx, _page, _ua = resources
        self.assertTrue(ctx.closed, "browser context must be closed")
        self.assertTrue(pw.stopped, "playwright must be stopped")

    async def test_invalid_cookie_is_rejected_before_any_row_is_created(self):
        called = AsyncMock()
        # Long enough to clear the schema's min_length, but not a usable
        # cookie — so the endpoint's own parser guard is what rejects it.
        garbage = "this is definitely not a linkedin cookie value"
        with patch.object(linkedin_api, "linkedin_login_with_cookies", called):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as ctx:
                    await linkedin_api.connect_linkedin_account_with_cookie(
                        payload=self._payload(cookie=garbage),
                        db=db,
                        current_user=_user(),
                    )
        self.assertEqual(ctx.exception.status_code, 400)
        called.assert_not_awaited()
        async with self.Session() as db:
            self.assertIsNone((await db.execute(select(LinkedInAccount))).scalars().first())

    async def test_expired_cookie_removes_the_account_row(self):
        with patch.object(
            linkedin_api,
            "linkedin_login_with_cookies",
            AsyncMock(
                return_value=(
                    LinkedInSessionStatus.EXPIRED,
                    None,
                    "LinkedIn rejected the imported session cookie.",
                )
            ),
        ):
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as ctx:
                    await linkedin_api.connect_linkedin_account_with_cookie(
                        payload=self._payload(), db=db, current_user=_user()
                    )
        self.assertEqual(ctx.exception.status_code, 400)
        # A failed import must not leave a half-connected row behind.
        async with self.Session() as db:
            self.assertIsNone((await db.execute(select(LinkedInAccount))).scalars().first())

    async def test_duplicate_account_conflicts(self):
        async with self.Session() as db:
            account = LinkedInAccount(
                owner_email="owner@test.dev",
                linkedin_email="existing@linkedin.dev",
                encrypted_password="x",
                status=LinkedInAccountStatus.ACTIVE,
            )
            account.assign_profile_dir()
            db.add(account)
            await db.commit()

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await linkedin_api.connect_linkedin_account_with_cookie(
                    payload=self._payload(), db=db, current_user=_user()
                )
        self.assertEqual(ctx.exception.status_code, 409)


class CookieAccountReloginGuardTests(unittest.IsolatedAsyncioTestCase):
    """An expired cookie account must not try to decrypt a NULL password."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            db.add(
                User(
                    first_name="Owner",
                    last_name="User",
                    email="owner@test.dev",
                    hashed_password="x",
                    is_verified=True,
                    role="customer",
                )
            )
            account = LinkedInAccount(
                owner_email="owner@test.dev",
                linkedin_email="cookie@linkedin.dev",
                encrypted_password=None,
                auth_method=AuthMethod.COOKIE.value,
                status=LinkedInAccountStatus.ACTIVE,
            )
            account.assign_profile_dir()
            db.add(account)
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_expired_cookie_session_asks_for_reimport(self):
        expired = SimpleNamespace(
            status=LinkedInSessionStatus.EXPIRED,
            message="Session expired",
            url="https://www.linkedin.com/login",
        )
        relogin = AsyncMock()

        with patch.object(linkedin_api, "acquire_profile_lock", lambda *a, **k: object()), \
             patch.object(linkedin_api, "release_profile_lock", lambda lock: None), \
             patch.object(
                 linkedin_api,
                 "launch_persistent_browser",
                 AsyncMock(return_value=(_FakePw(), None, _FakeCtx(), object())),
             ), \
             patch.object(linkedin_api, "verify_session", AsyncMock(return_value=expired)), \
             patch.object(linkedin_api, "linkedin_login", relogin), \
             patch.object(linkedin_api, "decrypt_credential") as decrypt:
            async with self.Session() as db:
                result = await linkedin_api.verify_linkedin_session(
                    db=db, current_user=_user()
                )

        self.assertEqual(result.status, "FAILED")
        self.assertIn("expired", result.message.lower())
        # Neither the password decrypt nor the credential relogin may run.
        decrypt.assert_not_called()
        relogin.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

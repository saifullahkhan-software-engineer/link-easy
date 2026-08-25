"""LinkedIn account connect must use the authenticated LinkEasy user.

A client-supplied owner_email must never win: otherwise one signed-in user
could attach a LinkedIn account to someone else's profile. Duplicate-account
protections stay in place.
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
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from automation.session import LinkedInSessionStatus  # noqa: E402
from core.config import settings  # noqa: E402
from database import Base  # noqa: E402
from models.linkedin_account import LinkedInAccount  # noqa: E402
from models.user import User  # noqa: E402
from schemas.linkedin import LinkedInAccountCreate  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(email="owner@test.dev"):
    return SimpleNamespace(email=email)


class _FakeResources:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True

    async def stop(self):
        self.closed = True


class LinkedInAccountOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()
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
            db.add(
                User(
                    first_name="Other",
                    last_name="User",
                    email="other@test.dev",
                    hashed_password="x",
                    is_verified=True,
                    role="customer",
                )
            )
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _payload(self, owner="attacker@example.com"):
        return LinkedInAccountCreate(
            owner_email=owner,
            linkedin_email="li@test.dev",
            linkedin_password="secret1",
            label="Work",
        )

    async def test_connect_ignores_payload_owner_email(self):
        from api.v1.linkedin import add_linkedin_account

        pw = _FakeResources()
        context = _FakeResources()

        async with self.Session() as db:
            with (
                patch("api.v1.linkedin.acquire_profile_lock", return_value=object()),
                patch("api.v1.linkedin.release_profile_lock"),
                patch("api.v1.linkedin.ensure_profile_dir"),
                patch(
                    "api.v1.linkedin.linkedin_login",
                    AsyncMock(
                        return_value=(
                            LinkedInSessionStatus.VALID,
                            (pw, None, context, object(), "ua"),
                            None,
                        )
                    ),
                ),
            ):
                response = await add_linkedin_account(
                    self._payload(owner="attacker@example.com"),
                    db,
                    _user("owner@test.dev"),
                )

        self.assertEqual(response.status, "LOGIN_SUCCESS")
        self.assertEqual(response.account.owner_email, "owner@test.dev")
        self.assertNotEqual(response.account.owner_email, "attacker@example.com")

    async def test_duplicate_owner_is_still_rejected(self):
        from api.v1.linkedin import add_linkedin_account

        async with self.Session() as db:
            existing = LinkedInAccount(
                owner_email="owner@test.dev",
                linkedin_email="already@test.dev",
                encrypted_password="enc",
                label="Old",
                profile_dir="/tmp/profiles/old",
                status="active",
            )
            existing.assign_profile_dir()
            db.add(existing)
            await db.commit()

            with self.assertRaises(HTTPException) as ctx:
                await add_linkedin_account(
                    LinkedInAccountCreate(
                        owner_email="someone-else@test.dev",
                        linkedin_email="new-li@test.dev",
                        linkedin_password="secret1",
                    ),
                    db,
                    _user("owner@test.dev"),
                )
        self.assertEqual(ctx.exception.status_code, 409)

    async def test_timeout_is_not_reported_as_bad_credentials(self):
        from api.v1.linkedin import add_linkedin_account

        async with self.Session() as db:
            with (
                patch("api.v1.linkedin.acquire_profile_lock", return_value=object()),
                patch("api.v1.linkedin.release_profile_lock"),
                patch("api.v1.linkedin.ensure_profile_dir"),
                patch(
                    "api.v1.linkedin.linkedin_login",
                    AsyncMock(
                        return_value=(
                            LinkedInSessionStatus.TIMEOUT,
                            None,
                            "LinkedIn stayed on the login page without accepting or rejecting the form.",
                        )
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await add_linkedin_account(
                        self._payload(), db, _user("owner@test.dev")
                    )

        self.assertEqual(ctx.exception.status_code, 504)
        self.assertNotIn("Invalid credentials", ctx.exception.detail)
        self.assertNotEqual(ctx.exception.status_code, 401)

    async def test_network_failure_is_a_502_not_401(self):
        from api.v1.linkedin import add_linkedin_account

        async with self.Session() as db:
            with (
                patch("api.v1.linkedin.acquire_profile_lock", return_value=object()),
                patch("api.v1.linkedin.release_profile_lock"),
                patch("api.v1.linkedin.ensure_profile_dir"),
                patch(
                    "api.v1.linkedin.linkedin_login",
                    AsyncMock(
                        return_value=(
                            LinkedInSessionStatus.NETWORK_ERROR,
                            None,
                            "Could not load LinkedIn (network or page-load failure).",
                        )
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await add_linkedin_account(
                        self._payload(), db, _user("owner@test.dev")
                    )

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertNotEqual(ctx.exception.status_code, 401)

    async def test_wrong_credentials_still_400(self):
        from api.v1.linkedin import add_linkedin_account

        async with self.Session() as db:
            with (
                patch("api.v1.linkedin.acquire_profile_lock", return_value=object()),
                patch("api.v1.linkedin.release_profile_lock"),
                patch("api.v1.linkedin.ensure_profile_dir"),
                patch(
                    "api.v1.linkedin.linkedin_login",
                    AsyncMock(
                        return_value=(
                            LinkedInSessionStatus.EXPIRED,
                            None,
                            "LinkedIn rejected the sign-in: Wrong email or password.",
                        )
                    ),
                ),
            ):
                with self.assertRaises(HTTPException) as ctx:
                    await add_linkedin_account(
                        self._payload(), db, _user("owner@test.dev")
                    )

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("Wrong email or password", ctx.exception.detail)


if __name__ == "__main__":
    unittest.main()

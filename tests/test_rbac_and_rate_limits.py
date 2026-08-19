"""Tests for role-based access, admin settings, and DB-backed rate limiting.

Covers the parts of the feature that are easy to get subtly wrong:

* the fixed-window limiter's arithmetic and its window rollover;
* the limiter failing **open** — a broken limiter must never lock users out;
* settings validation, including the hard caps that protect connected
  accounts from being flagged, and cross-field (min <= max) checks;
* role resolution: multi-role users, the legacy ``users.role`` fallback, and
  the rule that everyone keeps ``customer``;
* the admin API's role gating in both bootstrap and enforced modes.

Everything runs against SQLite in-memory. The upsert is also exercised
against real PostgreSQL during development; the SQL is written to be valid
on both (``ON CONFLICT ... DO UPDATE`` is shared syntax).
"""
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from database import Base  # noqa: E402
from models.rbac import Role, UserRoleLink  # noqa: E402
from models.user import User  # noqa: E402


async def _make_session():
    """A fresh in-memory database with every table created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_user(db, email, role="customer", verified=True):
    db.add(
        User(
            first_name="Test",
            last_name="User",
            email=email,
            hashed_password="x",
            is_verified=verified,
            role=role,
        )
    )
    await db.commit()


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_allows_up_to_the_limit_then_blocks(self):
        from services.rate_limiter import RateLimitRule, check_rate_limit

        rule = RateLimitRule("auth:login", max_requests=3, window_seconds=300)
        async with self.Session() as db:
            results = [
                await check_rate_limit(db, "ip:1.2.3.4", rule) for _ in range(5)
            ]

        self.assertEqual([r.allowed for r in results], [True, True, True, False, False])
        # Remaining counts down and never goes negative.
        self.assertEqual([r.remaining for r in results], [2, 1, 0, 0, 0])
        # A blocked caller is told when to come back.
        self.assertGreater(results[3].retry_after_seconds, 0)
        self.assertEqual(results[3].limit, 3)

    async def test_identities_are_counted_separately(self):
        from services.rate_limiter import RateLimitRule, check_rate_limit

        rule = RateLimitRule("auth:login", max_requests=1, window_seconds=300)
        async with self.Session() as db:
            first = await check_rate_limit(db, "ip:1.1.1.1", rule)
            second = await check_rate_limit(db, "ip:2.2.2.2", rule)
            third = await check_rate_limit(db, "ip:1.1.1.1", rule)

        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed, "a different IP must have its own budget")
        self.assertFalse(third.allowed)

    async def test_buckets_are_counted_separately(self):
        from services.rate_limiter import RateLimitRule, check_rate_limit

        login = RateLimitRule("auth:login", max_requests=1, window_seconds=300)
        scan = RateLimitRule("profile:scan", max_requests=1, window_seconds=300)
        async with self.Session() as db:
            self.assertTrue((await check_rate_limit(db, "user:a@b.c", login)).allowed)
            self.assertTrue(
                (await check_rate_limit(db, "user:a@b.c", scan)).allowed,
                "a separate bucket must not share the login budget",
            )

    async def test_new_window_resets_the_counter(self):
        from services.rate_limiter import RateLimitRule, check_rate_limit

        rule = RateLimitRule("auth:login", max_requests=2, window_seconds=60)
        now = datetime(2026, 8, 20, 12, 0, 30, tzinfo=timezone.utc)
        async with self.Session() as db:
            await check_rate_limit(db, "ip:9.9.9.9", rule, now=now)
            await check_rate_limit(db, "ip:9.9.9.9", rule, now=now)
            blocked = await check_rate_limit(db, "ip:9.9.9.9", rule, now=now)
            self.assertFalse(blocked.allowed)

            # Same identity, next window: the budget is fresh again.
            later = now + timedelta(seconds=60)
            self.assertTrue((await check_rate_limit(db, "ip:9.9.9.9", rule, now=later)).allowed)

    async def test_fails_open_when_the_database_is_unusable(self):
        """A broken limiter must allow traffic, not lock everyone out."""
        from services.rate_limiter import RateLimitRule, check_rate_limit

        rule = RateLimitRule("auth:login", max_requests=1, window_seconds=300)
        broken = AsyncMock()
        broken.execute.side_effect = RuntimeError("connection reset")
        # Even the rollback fails — the limiter must still not raise.
        broken.rollback.side_effect = RuntimeError("session is gone")

        result = await check_rate_limit(broken, "ip:1.2.3.4", rule)
        self.assertTrue(result.allowed)
        self.assertEqual(result.remaining, rule.max_requests)

    async def test_admin_override_changes_the_effective_limit(self):
        from services.app_settings import set_settings
        from services.rate_limiter import resolve_rule

        async with self.Session() as db:
            default = await resolve_rule(db, "auth:login")
            self.assertEqual(default.max_requests, 10)

            await set_settings(
                db,
                {"rate_limit.auth:login.max_requests": 3},
                updated_by="admin@test.dev",
            )
            tuned = await resolve_rule(db, "auth:login")

        self.assertEqual(tuned.max_requests, 3)
        self.assertEqual(tuned.bucket, "auth:login")

    async def test_every_default_bucket_resolves(self):
        from services.rate_limiter import DEFAULT_RULES, resolve_rule

        async with self.Session() as db:
            for bucket in DEFAULT_RULES:
                rule = await resolve_rule(db, bucket)
                self.assertEqual(rule.bucket, bucket)
                self.assertGreater(rule.max_requests, 0)
                self.assertGreater(rule.window_seconds, 0)


class AppSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_defaults_are_returned_before_anything_is_saved(self):
        from services.app_settings import get_settings_map

        async with self.Session() as db:
            values = await get_settings_map(db)

        self.assertEqual(values["campaign.daily_connection_limit"], 15)
        self.assertEqual(values["jobs.max_concurrent_browsers"], 2)

    async def test_saving_and_reading_back_a_value(self):
        from services.app_settings import get_setting, set_settings

        async with self.Session() as db:
            await set_settings(db, {"campaign.daily_message_limit": 12}, updated_by="a@b.c")
            self.assertEqual(await get_setting(db, "campaign.daily_message_limit"), 12)

    async def test_value_above_the_safety_cap_is_rejected(self):
        """Daily limits exist to stop accounts being flagged — never exceed them."""
        from services.app_settings import set_settings

        async with self.Session() as db:
            with self.assertRaises(ValueError) as ctx:
                await set_settings(db, {"campaign.daily_connection_limit": 500})
            self.assertIn("at most 15", str(ctx.exception))

    async def test_min_delay_cannot_exceed_max_delay(self):
        from services.app_settings import set_settings

        async with self.Session() as db:
            with self.assertRaises(ValueError) as ctx:
                await set_settings(
                    db,
                    {
                        "campaign.min_delay_seconds": 300,
                        "campaign.max_delay_seconds": 60,
                    },
                )
            self.assertIn("cannot be greater than", str(ctx.exception))

    async def test_cross_field_check_uses_the_stored_value_too(self):
        """Raising only the minimum must still be compared against the saved maximum."""
        from services.app_settings import set_settings

        async with self.Session() as db:
            await set_settings(db, {"campaign.max_delay_seconds": 90})
            with self.assertRaises(ValueError):
                await set_settings(db, {"campaign.min_delay_seconds": 120})

    async def test_unknown_key_is_rejected(self):
        from services.app_settings import set_settings

        async with self.Session() as db:
            with self.assertRaises(ValueError):
                await set_settings(db, {"campaign.not_a_real_setting": 1})

    async def test_describe_settings_exposes_bounds_for_the_ui(self):
        from services.app_settings import describe_settings, get_settings_map

        async with self.Session() as db:
            rows = describe_settings(await get_settings_map(db))

        by_key = {r["key"]: r for r in rows}
        conn_limit = by_key["campaign.daily_connection_limit"]
        self.assertEqual(conn_limit["maximum"], 15)
        self.assertEqual(conn_limit["category"], "campaign")
        self.assertTrue(conn_limit["description"])


class UserRoleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()
        async with self.Session() as db:
            db.add(Role(name="admin", description="Administrator"))
            db.add(Role(name="customer", description="Customer"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_falls_back_to_the_legacy_role_column(self):
        """Users created before the roles table still resolve correctly."""
        from services.user_roles import get_user_roles

        async with self.Session() as db:
            await _seed_user(db, "old-admin@test.dev", role="admin")
            roles = await get_user_roles(db, "old-admin@test.dev")

        self.assertIn("admin", roles)
        self.assertIn("customer", roles, "an admin can always use the app too")

    async def test_assigning_both_roles(self):
        from services.user_roles import get_user_roles, is_admin, set_user_roles

        async with self.Session() as db:
            await _seed_user(db, "dev@test.dev", role="customer")
            roles = await set_user_roles(db, "dev@test.dev", ["admin", "customer"])
            self.assertEqual(roles, ["admin", "customer"])
            self.assertTrue(await is_admin(db, "dev@test.dev"))

            # The denormalised column is kept in sync for legacy checks.
            refreshed = await db.get(User, "dev@test.dev")
            self.assertEqual(refreshed.role, "admin")
            self.assertEqual(await get_user_roles(db, "dev@test.dev"), ["admin", "customer"])

    async def test_revoking_admin_leaves_the_customer_role(self):
        from services.user_roles import is_admin, set_user_roles

        async with self.Session() as db:
            await _seed_user(db, "dev@test.dev", role="admin")
            await set_user_roles(db, "dev@test.dev", ["admin", "customer"])
            roles = await set_user_roles(db, "dev@test.dev", ["customer"])

            self.assertEqual(roles, ["customer"])
            self.assertFalse(await is_admin(db, "dev@test.dev"))
            refreshed = await db.get(User, "dev@test.dev")
            self.assertEqual(refreshed.role, "customer")

    async def test_unknown_role_is_rejected(self):
        from services.user_roles import set_user_roles

        async with self.Session() as db:
            await _seed_user(db, "dev@test.dev")
            with self.assertRaises(ValueError):
                await set_user_roles(db, "dev@test.dev", ["superuser"])

    async def test_unknown_user_is_rejected(self):
        from services.user_roles import set_user_roles

        async with self.Session() as db:
            with self.assertRaises(LookupError):
                await set_user_roles(db, "ghost@test.dev", ["customer"])

    async def test_setting_roles_twice_does_not_duplicate_links(self):
        from services.user_roles import set_user_roles
        from sqlalchemy import func, select

        async with self.Session() as db:
            await _seed_user(db, "dev@test.dev")
            await set_user_roles(db, "dev@test.dev", ["admin", "customer"])
            await set_user_roles(db, "dev@test.dev", ["admin", "customer"])
            count = (
                await db.execute(
                    select(func.count(UserRoleLink.role_id)).where(
                        UserRoleLink.user_email == "dev@test.dev"
                    )
                )
            ).scalar()

        self.assertEqual(count, 2)

    async def test_primary_role_prefers_admin(self):
        from services.user_roles import primary_role

        self.assertEqual(primary_role(["customer", "admin"]), "admin")
        self.assertEqual(primary_role(["customer"]), "customer")
        self.assertEqual(primary_role([]), "customer")


class AdminApiGatingTests(unittest.IsolatedAsyncioTestCase):
    """``require_admin`` must gate on roles once enforcement is switched on."""

    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()
        async with self.Session() as db:
            db.add(Role(name="admin", description="Administrator"))
            db.add(Role(name="customer", description="Customer"))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_bootstrap_mode_allows_any_signed_in_user(self):
        """Before roles are assigned nobody would be able to assign them."""
        from unittest.mock import patch

        from api.dependencies import require_admin

        async with self.Session() as db:
            await _seed_user(db, "plain@test.dev", role="customer")
            user = await db.get(User, "plain@test.dev")
            with patch("api.dependencies.settings.ADMIN_API_ENFORCED", False):
                self.assertIs(await require_admin(current_user=user, db=db), user)

    async def test_enforced_mode_blocks_a_customer(self):
        from unittest.mock import patch

        from fastapi import HTTPException

        from api.dependencies import require_admin

        async with self.Session() as db:
            await _seed_user(db, "plain@test.dev", role="customer")
            user = await db.get(User, "plain@test.dev")
            with patch("api.dependencies.settings.ADMIN_API_ENFORCED", True):
                with self.assertRaises(HTTPException) as ctx:
                    await require_admin(current_user=user, db=db)
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_enforced_mode_allows_an_admin(self):
        from unittest.mock import patch

        from api.dependencies import require_admin
        from services.user_roles import set_user_roles

        async with self.Session() as db:
            await _seed_user(db, "boss@test.dev", role="customer")
            await set_user_roles(db, "boss@test.dev", ["admin", "customer"])
            user = await db.get(User, "boss@test.dev")
            with patch("api.dependencies.settings.ADMIN_API_ENFORCED", True):
                self.assertIs(await require_admin(current_user=user, db=db), user)


if __name__ == "__main__":
    unittest.main()

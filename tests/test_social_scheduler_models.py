"""Social scheduler models: ownership, per-user platform uniqueness, cascades.

The standalone social_scheduler/ service was single-tenant (one YouTube /
Instagram / TikTok connection per deployment) and its timestamp columns used
the *string* "now()", which broke every UPDATE. These tests pin the behaviour
of the models after the merge into the main app's schema.
"""
import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

from sqlalchemy import event, select  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from database import Base  # noqa: E402
import models  # noqa: E402,F401  (registers every table on Base)
from models.social_scheduler import (  # noqa: E402
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
    SocialPostStatus,
)
from models.user import User  # noqa: E402

OWNER = "owner@test.dev"
OTHER = "other@test.dev"


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    # SQLite ignores ON DELETE CASCADE unless foreign keys are switched on.
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(email):
    return User(
        first_name="T", last_name="U", email=email, hashed_password="x",
        is_verified=True, role="customer",
    )


def _post(owner=OWNER, **overrides):
    values = dict(
        owner_email=owner,
        title="Launch teaser",
        caption="We're live",
        video_path="uploads/social/abc.mp4",
        video_url="/uploads/social/abc.mp4",
        platforms=["youtube", "tiktok"],
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    values.update(overrides)
    return SocialPost(**values)


class SocialSchedulerModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()
        async with self.Session() as db:
            db.add_all([_user(OWNER), _user(OTHER)])
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_tables_are_registered_on_the_main_base(self):
        for name in ("social_posts", "social_post_results", "social_platform_connections"):
            self.assertIn(name, Base.metadata.tables)

    async def test_post_defaults_and_update_round_trip(self):
        # The original schema's onupdate="now()" string literal made every
        # UPDATE fail; func.now() must survive insert *and* update.
        async with self.Session() as db:
            post = _post()
            db.add(post)
            await db.commit()
            await db.refresh(post)
            self.assertEqual(post.status, SocialPostStatus.PENDING.value)
            self.assertEqual(post.hashtags, "")
            self.assertIsNotNone(post.created_at)
            self.assertIsNotNone(post.updated_at)
            self.assertEqual(post.results, [])

            post.status = SocialPostStatus.POSTING.value
            await db.commit()
            await db.refresh(post)
            self.assertEqual(post.status, SocialPostStatus.POSTING.value)

    async def test_results_are_eager_loaded_and_cascade_on_delete(self):
        async with self.Session() as db:
            post = _post()
            db.add(post)
            await db.flush()
            db.add_all([
                SocialPostResult(post_id=post.id, owner_email=OWNER, platform="youtube"),
                SocialPostResult(post_id=post.id, owner_email=OWNER, platform="tiktok"),
            ])
            await db.commit()
            post_id = post.id

        # A fresh session: accessing .results must not lazy-load (which would
        # raise MissingGreenlet under AsyncSession) — selectin loads it up front.
        async with self.Session() as db:
            loaded = (await db.execute(select(SocialPost).where(SocialPost.id == post_id))).scalar_one()
            self.assertEqual([r.platform for r in loaded.results], ["tiktok", "youtube"])
            await db.delete(loaded)
            await db.commit()

        async with self.Session() as db:
            remaining = (await db.execute(select(SocialPostResult))).scalars().all()
            self.assertEqual(remaining, [])

    async def test_one_result_per_post_and_platform(self):
        async with self.Session() as db:
            post = _post()
            db.add(post)
            await db.flush()
            db.add(SocialPostResult(post_id=post.id, owner_email=OWNER, platform="youtube"))
            await db.commit()
            db.add(SocialPostResult(post_id=post.id, owner_email=OWNER, platform="youtube"))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_platform_connection_is_unique_per_user_not_per_deployment(self):
        async with self.Session() as db:
            db.add(SocialPlatformConnection(
                owner_email=OWNER, platform="youtube", encrypted_access_token="enc"
            ))
            # A second user connecting the same platform must be allowed.
            db.add(SocialPlatformConnection(
                owner_email=OTHER, platform="youtube", encrypted_access_token="enc"
            ))
            await db.commit()

            # ...but the same user connecting the same platform twice is not.
            db.add(SocialPlatformConnection(
                owner_email=OWNER, platform="youtube", encrypted_access_token="enc2"
            ))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_rows_require_an_owner(self):
        async with self.Session() as db:
            db.add(_post(owner=None))
            with self.assertRaises(IntegrityError):
                await db.commit()

    async def test_deleting_a_user_removes_their_social_data(self):
        async with self.Session() as db:
            post = _post()
            db.add(post)
            await db.flush()
            db.add(SocialPostResult(post_id=post.id, owner_email=OWNER, platform="youtube"))
            db.add(SocialPlatformConnection(
                owner_email=OWNER, platform="youtube", encrypted_access_token="enc"
            ))
            db.add(_post(owner=OTHER))
            await db.commit()

        async with self.Session() as db:
            owner = (await db.execute(select(User).where(User.email == OWNER))).scalar_one()
            await db.delete(owner)
            await db.commit()

        async with self.Session() as db:
            posts = (await db.execute(select(SocialPost))).scalars().all()
            self.assertEqual([p.owner_email for p in posts], [OTHER])
            self.assertEqual((await db.execute(select(SocialPostResult))).scalars().all(), [])
            self.assertEqual((await db.execute(select(SocialPlatformConnection))).scalars().all(), [])


if __name__ == "__main__":
    unittest.main()

"""Facebook Groups: the manual share checklist.

Meta removed the Groups API on 22 Apr 2024 — ``publish_to_groups`` and the
group feed endpoints went with it, across every Graph API version, with no
replacement announced. So the only honest feature is a checklist: the user
picks the groups, the Reel publishes to the Page, and the post then lists what
still needs sharing by hand.

These tests pin the two halves and, just as importantly, the boundary: nothing
here may ever claim to have posted to a group.

* ``/share-targets`` — a per-user bookmark list (name + URL). Re-adding a URL
  is a no-op rather than a 409, another user's rows are invisible, and only
  http(s) links are accepted because the UI renders them as anchors.
* ``POST/PATCH /posts`` — ``facebook_groups`` is stored as a *snapshot*, so
  deleting a saved target never blanks an older post's checklist.
"""
import asyncio
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
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_current_user, get_db  # noqa: E402
from api.v1.social_scheduler import router  # noqa: E402
from core.config import settings  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.social_scheduler import ShareTarget  # noqa: E402
from models.user import User  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64
settings.JWT_SECRET = "test-secret"

OWNER = "owner@test.dev"
OTHER = "other@test.dev"
GROUP_URL = "https://www.facebook.com/groups/1234567890"


def _user(email):
    return User(first_name="T", last_name="U", email=email, hashed_password="x", is_verified=True, role="customer")


class GroupSharingApiTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        self.upload_dir = tempfile.mkdtemp(prefix="le-groups-")
        self._settings_patch = patch.multiple(
            settings,
            UPLOAD_DIR=self.upload_dir,
            MAX_UPLOAD_SIZE=1024 * 1024,
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

    async def _upload_id(self, client):
        res = await client.post(
            "/api/v1/social-scheduler/upload", files={"file": ("clip.mp4", b"\x00" * 2048, "video/mp4")}
        )
        assert res.status_code == 200, res.text
        return res.json()["upload_id"]

    async def _create(self, client, upload_id, **overrides):
        body = {
            "title": "Launch teaser",
            "caption": "We're live",
            "hashtags": "#launch",
            "upload_id": upload_id,
            "platforms": ["facebook"],
            "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        }
        body.update(overrides)
        return await client.post("/api/v1/social-scheduler/posts", json=body)

    # ── saved destinations ───────────────────────────────────────────────────

    def test_a_group_can_be_saved_listed_and_removed(self):
        async def run(client):
            created = await client.post(
                "/api/v1/social-scheduler/share-targets", json={"name": "Lahore Freelancers", "url": GROUP_URL}
            )
            self.assertEqual(created.status_code, 201, created.text)
            target_id = created.json()["id"]
            self.assertEqual(created.json()["platform"], "facebook")
            self.assertEqual(created.json()["url"], GROUP_URL)

            listed = await client.get("/api/v1/social-scheduler/share-targets")
            self.assertEqual([t["name"] for t in listed.json()], ["Lahore Freelancers"])

            removed = await client.delete(f"/api/v1/social-scheduler/share-targets/{target_id}")
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual((await client.get("/api/v1/social-scheduler/share-targets")).json(), [])

        self.run_async(run)

    def test_saving_the_same_group_twice_is_a_no_op(self):
        """The picker's inline "add" field is used casually — re-adding what is
        already saved must select it, not error."""

        async def run(client):
            first = await client.post(
                "/api/v1/social-scheduler/share-targets", json={"name": "Group", "url": GROUP_URL}
            )
            second = await client.post(
                "/api/v1/social-scheduler/share-targets", json={"name": "Group (renamed)", "url": GROUP_URL}
            )
            self.assertEqual(second.status_code, 201, second.text)
            self.assertEqual(second.json()["id"], first.json()["id"])
            # the rename is kept, so the label can be corrected in place
            self.assertEqual(second.json()["name"], "Group (renamed)")
            self.assertEqual(len((await client.get("/api/v1/social-scheduler/share-targets")).json()), 1)

        self.run_async(run)

    def test_only_http_links_are_accepted(self):
        """The URL is rendered as an <a href>, so a javascript: URL would be a
        stored XSS the first time a checklist opens."""

        async def run(client):
            for url in ["javascript:alert(1)", "data:text/html,x", "ftp://example.com", "not a url", ""]:
                res = await client.post(
                    "/api/v1/social-scheduler/share-targets", json={"name": "Bad", "url": url}
                )
                self.assertEqual(res.status_code, 422, f"{url!r} was accepted")

        self.run_async(run)

    def test_saved_groups_are_scoped_to_their_owner(self):
        async def run(client):
            await client.post("/api/v1/social-scheduler/share-targets", json={"name": "Mine", "url": GROUP_URL})
            listed = await client.get("/api/v1/social-scheduler/share-targets")
            target_id = listed.json()[0]["id"]

            self.current_email = OTHER
            self.assertEqual((await client.get("/api/v1/social-scheduler/share-targets")).json(), [])
            # 404, not 403 — no existence oracle
            res = await client.delete(f"/api/v1/social-scheduler/share-targets/{target_id}")
            self.assertEqual(res.status_code, 404)

        self.run_async(run)

    def test_an_unknown_platform_is_rejected(self):
        async def run(client):
            res = await client.get("/api/v1/social-scheduler/share-targets", params={"platform": "myspace"})
            self.assertEqual(res.status_code, 422)

        self.run_async(run)

    # ── the per-post snapshot ────────────────────────────────────────────────

    def test_chosen_groups_are_stored_on_the_post(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(
                client,
                upload_id,
                facebook_groups=[{"name": "Lahore Freelancers", "url": GROUP_URL}],
            )
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(
                res.json()["facebook_groups"], [{"name": "Lahore Freelancers", "url": GROUP_URL}]
            )

            fetched = await client.get(f"/api/v1/social-scheduler/posts/{res.json()['id']}")
            self.assertEqual(len(fetched.json()["facebook_groups"]), 1)

        self.run_async(run)

    def test_deleting_a_saved_target_leaves_the_posts_checklist_intact(self):
        """The post stores name + url, not an id — history must not blank out
        when a destination is removed from the saved list."""

        async def run(client):
            saved = await client.post(
                "/api/v1/social-scheduler/share-targets", json={"name": "Lahore Freelancers", "url": GROUP_URL}
            )
            upload_id = await self._upload_id(client)
            created = await self._create(
                client, upload_id, facebook_groups=[{"name": "Lahore Freelancers", "url": GROUP_URL}]
            )
            post_id = created.json()["id"]

            await client.delete(f"/api/v1/social-scheduler/share-targets/{saved.json()['id']}")
            fetched = await client.get(f"/api/v1/social-scheduler/posts/{post_id}")
            self.assertEqual(
                fetched.json()["facebook_groups"], [{"name": "Lahore Freelancers", "url": GROUP_URL}]
            )

        self.run_async(run)

    def test_duplicate_groups_collapse_to_one_row(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(
                client,
                upload_id,
                facebook_groups=[
                    {"name": "A", "url": GROUP_URL},
                    {"name": "A again", "url": GROUP_URL},
                    {"name": "B", "url": "https://www.facebook.com/groups/999"},
                ],
            )
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual([g["url"] for g in res.json()["facebook_groups"]], [GROUP_URL, "https://www.facebook.com/groups/999"])

        self.run_async(run)

    def test_more_than_twenty_five_groups_is_rejected(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(
                client,
                upload_id,
                facebook_groups=[{"name": f"G{i}", "url": f"https://www.facebook.com/groups/{i}"} for i in range(26)],
            )
            self.assertEqual(res.status_code, 422)
            self.assertIn("at most 25", res.text)

        self.run_async(run)

    def test_a_group_link_on_a_post_must_be_http(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id, facebook_groups=[{"name": "X", "url": "javascript:alert(1)"}])
            self.assertEqual(res.status_code, 422)

        self.run_async(run)

    def test_the_selection_can_be_edited_and_cleared(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            created = await self._create(client, upload_id, facebook_groups=[{"name": "A", "url": GROUP_URL}])
            post_id = created.json()["id"]

            edited = await client.patch(
                f"/api/v1/social-scheduler/posts/{post_id}",
                json={"facebook_groups": [{"name": "B", "url": "https://www.facebook.com/groups/999"}]},
            )
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual([g["name"] for g in edited.json()["facebook_groups"]], ["B"])

            cleared = await client.patch(f"/api/v1/social-scheduler/posts/{post_id}", json={"facebook_groups": []})
            self.assertEqual(cleared.json()["facebook_groups"], [])

        self.run_async(run)

    def test_a_post_without_groups_defaults_to_an_empty_checklist(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            res = await self._create(client, upload_id)
            self.assertEqual(res.status_code, 201, res.text)
            self.assertEqual(res.json()["facebook_groups"], [])

        self.run_async(run)

    def test_another_user_cannot_attach_groups_to_a_post(self):
        async def run(client):
            upload_id = await self._upload_id(client)
            created = await self._create(client, upload_id, facebook_groups=[{"name": "A", "url": GROUP_URL}])
            post_id = created.json()["id"]

            self.current_email = OTHER
            res = await client.patch(
                f"/api/v1/social-scheduler/posts/{post_id}",
                json={"facebook_groups": [{"name": "Evil", "url": "https://evil.example.com"}]},
            )
            self.assertEqual(res.status_code, 404)

        self.run_async(run)


class GroupShareNoteTests(unittest.TestCase):
    """The worker's part: publish to the Page, then *say* what is still manual."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="le-groups-worker-")
        os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{os.path.join(self._tmp, 'w.db')}"
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from worker.tasks import social_scheduler_tasks as tasks

        self.tasks = tasks
        self.engine = create_engine(f"sqlite:///{os.path.join(self._tmp, 'w.db')}", connect_args={"check_same_thread": False})
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        self._orig_factory = tasks.SyncSession
        tasks.SyncSession = self.Session
        Base.metadata.create_all(self.engine)
        self.video = os.path.join(self._tmp, "clip.mp4")
        with open(self.video, "wb") as fh:
            fh.write(b"\x00" * 1024)
        with self.Session() as s:
            s.add(_user(OWNER))
            s.commit()

    def tearDown(self):
        self.tasks.SyncSession = self._orig_factory
        self.engine.dispose()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_a_facebook_publish_names_the_groups_left_to_share(self):
        from models.social_scheduler import SocialPlatformConnection, SocialPost, SocialPostResult
        from core.security import encrypt_credential

        with self.Session() as s:
            s.add(
                SocialPlatformConnection(
                    owner_email=OWNER,
                    platform="facebook",
                    account_id="page-1",
                    account_name="My Page",
                    encrypted_access_token=encrypt_credential("page-token"),
                )
            )
            post = SocialPost(
                owner_email=OWNER,
                title="Teaser",
                caption="Hello",
                hashtags="#hi",
                video_path=self.video,
                video_url="http://localhost:8000/x.mp4",
                platforms=["facebook"],
                scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                facebook_groups=[{"name": "Lahore Freelancers", "url": GROUP_URL}],
            )
            s.add(post)
            s.commit()
            post_id = post.id

        fb = AsyncMock(return_value={"video_id": "vid-1", "video_url": "https://facebook.com/watch/?v=vid-1"})
        with patch("services.social.facebook.FacebookService.upload_video", fb):
            outcome = self.tasks.publish_post(post_id)

        self.assertEqual(outcome["status"], "posted", outcome)
        with self.Session() as s:
            result = s.query(SocialPostResult).filter_by(post_id=post_id, platform="facebook").one()
            self.assertEqual(result.status, "posted")
            self.assertEqual(result.error, "")
            self.assertIn("Share it manually to 1 group", result.note)

    def test_no_groups_means_no_checklist_note(self):
        from models.social_scheduler import SocialPlatformConnection, SocialPost, SocialPostResult
        from core.security import encrypt_credential

        with self.Session() as s:
            s.add(
                SocialPlatformConnection(
                    owner_email=OWNER,
                    platform="facebook",
                    account_id="page-1",
                    account_name="My Page",
                    encrypted_access_token=encrypt_credential("page-token"),
                )
            )
            post = SocialPost(
                owner_email=OWNER,
                title="Teaser",
                caption="Hello",
                hashtags="",
                video_path=self.video,
                video_url="",
                platforms=["facebook"],
                scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            s.add(post)
            s.commit()
            post_id = post.id

        fb = AsyncMock(return_value={"video_id": "vid-1", "video_url": "https://facebook.com/watch/?v=vid-1"})
        with patch("services.social.facebook.FacebookService.upload_video", fb):
            self.tasks.publish_post(post_id)

        with self.Session() as s:
            result = s.query(SocialPostResult).filter_by(post_id=post_id, platform="facebook").one()
            self.assertEqual(result.status, "posted")
            self.assertEqual(result.note, "")


if __name__ == "__main__":
    unittest.main()

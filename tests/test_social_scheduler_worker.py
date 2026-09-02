"""Worker: publish_social_post against a real (SQLite) database.

Platform services are mocked at the boundary; everything else — the atomic
pending→posting claim, per-platform result rows, token refresh + encrypted
persistence, and the final post status — runs for real.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

_tmp = tempfile.mkdtemp(prefix="le-social-worker-")
_DB = os.path.join(_tmp, "worker.db")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_DB}"
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost:5173")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test-secret")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.config import settings  # noqa: E402
from core.security import decrypt_credential, encrypt_credential  # noqa: E402
from database import Base  # noqa: E402
import models  # noqa: E402,F401
from models.social_scheduler import SocialPlatformConnection, SocialPost, SocialPostResult  # noqa: E402
from models.user import User  # noqa: E402

settings.CREDENTIAL_ENCRYPTION_KEY = "a" * 64

from worker.tasks import social_scheduler_tasks as tasks  # noqa: E402

OWNER = "owner@test.dev"


class PublishSocialPostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The task module builds its engine from whatever DATABASE_URL the
        # process-wide Settings singleton held when it was first imported
        # (another test module may have won that race), so point its session
        # factory at this test's own SQLite file explicitly.
        cls.engine = create_engine(f"sqlite:///{_DB}", connect_args={"check_same_thread": False})
        cls.Session = sessionmaker(bind=cls.engine, expire_on_commit=False)
        cls._orig_factory = tasks.SyncSession
        tasks.SyncSession = cls.Session

    @classmethod
    def tearDownClass(cls):
        tasks.SyncSession = cls._orig_factory
        cls.engine.dispose()

    def setUp(self):
        Base.metadata.drop_all(self.engine)
        Base.metadata.create_all(self.engine)
        self.video = os.path.join(_tmp, "clip.mp4")
        with open(self.video, "wb") as fh:
            fh.write(b"\x00" * 1024)
        with self.Session() as s:
            s.add(User(first_name="T", last_name="U", email=OWNER, hashed_password="x", is_verified=True, role="customer"))
            s.commit()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _post(self, platforms, **kw):
        fields = dict(
            owner_email=OWNER, title="Teaser", caption="Hello", hashtags="#hi",
            video_path=self.video, video_url="https://api.example.com/uploads/social/clip.mp4",
            platforms=platforms, scheduled_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        fields.update(kw)
        with self.Session() as s:
            post = SocialPost(**fields)
            s.add(post)
            s.commit()
            return post.id

    def _connect(self, platform, *, expires_in=None, refresh="rt", account_id="acct"):
        with self.Session() as s:
            s.add(SocialPlatformConnection(
                owner_email=OWNER, platform=platform, account_id=account_id, account_name="me",
                encrypted_access_token=encrypt_credential("old-access"),
                encrypted_refresh_token=encrypt_credential(refresh) if refresh else None,
                expires_at=(datetime.now(timezone.utc) + timedelta(seconds=expires_in)) if expires_in is not None else None,
            ))
            s.commit()

    def _state(self, post_id):
        with self.Session() as s:
            post = s.get(SocialPost, post_id)
            results = {r.platform: r for r in s.query(SocialPostResult).filter_by(post_id=post_id).all()}
            return post.status, results

    # ── tests ────────────────────────────────────────────────────────────────

    def test_publishes_to_every_platform_and_records_results(self):
        post_id = self._post(["youtube", "tiktok"], youtube_title="YT title", tiktok_caption="TT caption")
        self._connect("youtube", expires_in=3600)
        self._connect("tiktok", expires_in=3600)
        yt = AsyncMock(return_value={"video_id": "vid1", "video_url": "https://www.youtube.com/shorts/vid1"})
        tt = AsyncMock(return_value={"publish_id": "pub1", "video_url": "https://www.tiktok.com/@me/video/pub1"})
        with patch("services.social.youtube.YouTubeService.upload_short", yt), \
             patch("services.social.tiktok.TikTokService.upload_video", tt):
            outcome = tasks.publish_post(post_id)

        self.assertEqual(outcome["status"], "posted")
        status, results = self._state(post_id)
        self.assertEqual(status, "posted")
        self.assertEqual(results["youtube"].status, "posted")
        self.assertEqual(results["youtube"].platform_url, "https://www.youtube.com/shorts/vid1")
        self.assertIsNotNone(results["youtube"].posted_at)
        self.assertEqual(results["tiktok"].platform_id, "pub1")
        # Decrypted tokens and per-platform copy reach the services.
        self.assertEqual(yt.call_args.kwargs["access_token"], "old-access")
        self.assertEqual(yt.call_args.kwargs["title"], "YT title")
        self.assertEqual(yt.call_args.kwargs["description"], "Hello\n\n#hi")
        self.assertEqual(tt.call_args.kwargs["caption"], "TT caption")
        self.assertEqual(tt.call_args.kwargs["video_path"], self.video)

    def test_partial_failure_marks_post_failed_but_keeps_successes(self):
        post_id = self._post(["youtube", "instagram"])
        self._connect("youtube", expires_in=3600)
        # Instagram deliberately NOT connected.
        yt = AsyncMock(return_value={"video_id": "vid1", "video_url": "u"})
        with patch("services.social.youtube.YouTubeService.upload_short", yt):
            outcome = tasks.publish_post(post_id)

        self.assertEqual(outcome["status"], "failed")
        status, results = self._state(post_id)
        self.assertEqual(status, "failed")
        self.assertEqual(results["youtube"].status, "posted")
        self.assertEqual(results["instagram"].status, "failed")
        self.assertIn("not connected", results["instagram"].error)

    def test_expired_token_is_refreshed_and_persisted_before_upload(self):
        post_id = self._post(["tiktok"])
        self._connect("tiktok", expires_in=-10, refresh="rt-old")
        refresh = AsyncMock(return_value={"access_token": "new-access", "refresh_token": "rt-new", "expires_in": 86400})
        upload = AsyncMock(return_value={"publish_id": "p", "video_url": "u"})
        with patch("services.social.tiktok.TikTokService.refresh_access_token", refresh), \
             patch("services.social.tiktok.TikTokService.upload_video", upload):
            outcome = tasks.publish_post(post_id)

        self.assertEqual(outcome["status"], "posted")
        refresh.assert_awaited_once()
        self.assertEqual(refresh.call_args.args[0], "rt-old")
        self.assertEqual(upload.call_args.kwargs["access_token"], "new-access")
        with self.Session() as s:
            conn = s.query(SocialPlatformConnection).one()
            self.assertEqual(decrypt_credential(conn.encrypted_access_token), "new-access")
            self.assertEqual(decrypt_credential(conn.encrypted_refresh_token), "rt-new")
            self.assertGreater(conn.expires_at.replace(tzinfo=timezone.utc), datetime.now(timezone.utc) + timedelta(hours=23))

    def test_unrefreshable_token_fails_that_platform_with_reconnect_hint(self):
        post_id = self._post(["youtube"])
        self._connect("youtube", expires_in=-10, refresh=None)
        upload = AsyncMock(side_effect=AssertionError("must not upload with an expired token"))
        with patch("services.social.youtube.YouTubeService.upload_short", upload):
            outcome = tasks.publish_post(post_id)

        self.assertEqual(outcome["status"], "failed")
        _, results = self._state(post_id)
        self.assertIn("Reconnect YouTube", results["youtube"].error)
        self.assertIn("could not be renewed", results["youtube"].error)

    def test_only_pending_posts_are_claimed(self):
        for state in ("cancelled", "posting", "posted"):
            post_id = self._post(["youtube"], status=state)
            with patch("services.social.youtube.YouTubeService.upload_short", AsyncMock(side_effect=AssertionError)):
                outcome = tasks.publish_post(post_id)
            self.assertEqual(outcome["status"], "skipped", state)
            self.assertEqual(outcome["current_status"], state)
            self.assertEqual(self._state(post_id)[0], state)

    def test_missing_video_file_is_reported_not_raised(self):
        post_id = self._post(["youtube"])
        self._connect("youtube", expires_in=3600)
        os.remove(self.video)
        from services.social.youtube import YouTubeService

        # Real service (no mock) so the file check runs for real.
        outcome = tasks.publish_post(post_id)
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("missing on the server", self._state(post_id)[1]["youtube"].error)
        self.assertIsNotNone(YouTubeService)

    def test_instagram_requires_a_public_video_url(self):
        post_id = self._post(["instagram"])
        with self.Session() as s:
            s.get(SocialPost, post_id).video_url = "http://localhost:8000/uploads/social/clip.mp4"
            s.commit()
        self._connect("instagram", expires_in=3600, refresh=None)
        with patch("services.social.instagram.InstagramService.publish_reel", AsyncMock(side_effect=AssertionError)):
            outcome = tasks.publish_post(post_id)
        self.assertEqual(outcome["status"], "failed")
        self.assertIn("PUBLIC_API_URL", self._state(post_id)[1]["instagram"].error)

    def test_dispatcher_queues_each_due_post_once_under_a_lease(self):
        due = self._post(["youtube"])
        self._post(["youtube"], scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1))  # not due
        self._post(["youtube"], status="cancelled")
        sent = []
        leases = {}

        def claim(client, key, timeout):
            if key in leases:
                return None
            leases[key] = "tok"
            return "tok"

        with patch.object(tasks, "claim_dispatch_lease", side_effect=claim), \
             patch.object(tasks.celery_app, "send_task", side_effect=lambda name, args: sent.append((name, args))), \
             patch("redis.from_url"):
            first = tasks.dispatch_due_social_posts()
            second = tasks.dispatch_due_social_posts()

        self.assertEqual(first["posts_dispatched"], 1)
        self.assertEqual(sent, [("tasks.publish_social_post", [due, "tok"])])
        self.assertEqual(second["posts_dispatched"], 0, "lease held → not re-queued")
        self.assertEqual(tasks._lease_key(due), f"linkeasy:scheduler:social:{due}")

    def test_dispatcher_resets_posts_stuck_in_posting(self):
        stuck = self._post(["youtube"], status="posting")
        with self.Session() as s:
            row = s.get(SocialPost, stuck)
            row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=tasks.STALE_POSTING_SECONDS + 60)
            s.commit()
        with patch.object(tasks, "claim_dispatch_lease", return_value="tok"), \
             patch.object(tasks.celery_app, "send_task"), patch("redis.from_url"):
            tasks.dispatch_due_social_posts()
        self.assertEqual(self._state(stuck)[0], "pending")

    def test_sync_url_rewrite_matches_the_other_task_modules(self):
        self.assertEqual(
            tasks._make_sync_url("postgresql+asyncpg://u:p@h/db"), "postgresql+psycopg2://u:p@h/db"
        )
        self.assertEqual(tasks._make_sync_url("postgresql://u:p@h/db"), "postgresql+psycopg2://u:p@h/db")


if __name__ == "__main__":
    unittest.main()

"""
Tests for Feed Scroll job scheduling, pause/resume remaining time preservation,
and overdue scan dispatching.
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

_required_env = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "JWT_SECRET": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "PASSWORD_RESET_URL": "http://localhost/reset",
    "BACKEND_CORS_ORIGINS": "http://localhost:5173",
    "RESEND_API_KEY": "test",
    "FROM_EMAIL": "test@example.com",
    "REDIS_URL": "redis://localhost:6379/0",
}
for key, value in _required_env.items():
    os.environ.setdefault(key, value)

# Stub patchright before feed-scroll router imports it at module load.
import types  # noqa: E402

_patchright = types.ModuleType("patchright")
_async_api = types.ModuleType("patchright.async_api")
for _name in ("Page", "Browser", "BrowserContext", "Locator", "ElementHandle", "TimeoutError", "Error", "async_playwright"):
    setattr(_async_api, _name, type(_name, (), {}))
_patchright.async_api = _async_api
sys.modules.setdefault("patchright", _patchright)
sys.modules.setdefault("patchright.async_api", _async_api)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_db  # noqa: E402
from api.v1.feed_scroll import router as feed_scroll_router  # noqa: E402
from database import Base  # noqa: E402
from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus, FeedScrollMode  # noqa: E402
from models.feed_scroll_result import FeedScrollResult  # noqa: E402
from models.feed_scroll_applied_post import FeedScrollAppliedPost  # noqa: E402
from schemas.feed_scroll import FeedScrollJobResponse  # noqa: E402

OWNER = "owner@test.dev"
JOB_ID_1 = "job-pause-resume"
JOB_ID_2 = "job-overdue"
RESULT_ID_1 = "res-for-apply-1"


class FeedScrollSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

        app = FastAPI()
        app.include_router(feed_scroll_router)

        async def override_get_db():
            async with self.session_factory() as session:
                yield session

        app.dependency_overrides[get_db] = override_get_db
        self.app = app
        self.loop.run_until_complete(self._seed())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    async def _seed(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with self.session_factory() as session:
            now = datetime.now(timezone.utc)
            # Active job with 30 minutes left until next scan
            session.add(FeedScrollJob(
                id=JOB_ID_1,
                account_email="li@test.dev",
                owner_email=OWNER,
                name="Active scan job",
                mode=FeedScrollMode.JOB_SEARCH,
                status=FeedScrollJobStatus.ACTIVE,
                feed_interval_hours=2,
                posts_per_scan=20,
                job_titles=["Backend Engineer"],
                last_scanned_at=now - timedelta(seconds=5400),
                next_scan_at=now + timedelta(seconds=1800),
            ))
            # Active job whose next scan is overdue (yesterday at 8pm)
            # Active job whose next scan is overdue (yesterday at 8pm)
            session.add(FeedScrollJob(
                id=JOB_ID_2,
                account_email="li@test.dev",
                owner_email=OWNER,
                name="Overdue scan job",
                mode=FeedScrollMode.JOB_SEARCH,
                status=FeedScrollJobStatus.ACTIVE,
                feed_interval_hours=4,
                posts_per_scan=20,
                job_titles=["Python Developer"],
                last_scanned_at=now - timedelta(hours=16),
                next_scan_at=now - timedelta(hours=12),
            ))
            # Scored post result for testing mark-as-applied
            session.add(FeedScrollResult(
                id=RESULT_ID_1,
                feed_scroll_job_id=JOB_ID_1,
                post_urn="urn:li:activity:7999888777666555444",
                post_url="https://www.linkedin.com/feed/update/urn:li:activity:7999888777666555444/",
                author_name="Alice Recruiter",
                author_first_name="Alice",
                author_last_name="Recruiter",
                author_profile_url="https://www.linkedin.com/in/alicerecruiter",
                post_text="We have a great Backend Engineer role available!",
                score=9.5,
                matched_terms=["Backend Engineer"],
                scan_batch_id="batch-test-1",
                scanned_at=now,
            ))
            await session.commit()

    def run_async(self, coro_factory):
        async def runner():
            transport = ASGITransport(app=self.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await coro_factory(client)

        return self.loop.run_until_complete(runner())

    def test_pause_saves_remaining_time_and_resume_starts_countdown(self):
        """Pausing an active job preserves the remaining seconds, and activating
        resumes the scan countdown from now + remaining_seconds."""
        async def scenario(client):
            # 1. Pause job
            res = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/pause",
                params={"owner_email": OWNER},
            )
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn("paused", data["message"])
            self.assertIsNotNone(data["remaining_seconds"])
            self.assertTrue(1700 <= data["remaining_seconds"] <= 1850)

            # Check DB record
            async with self.session_factory() as session:
                from sqlalchemy import select
                result = await session.execute(
                    select(FeedScrollJob).where(FeedScrollJob.id == JOB_ID_1)
                )
                job = result.scalars().first()
                self.assertEqual(job.status, FeedScrollJobStatus.PAUSED)
                self.assertIsNotNone(job.remaining_seconds)
                self.assertTrue(1700 <= job.remaining_seconds <= 1850)

            # 2. Activate / resume job
            with patch("worker.celery_app.celery_app.send_task") as mock_send_task:
                res = await client.post(
                    f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/activate",
                    params={"owner_email": OWNER},
                )
                self.assertEqual(res.status_code, 200)
                data = res.json()
                self.assertIn("resumed", data["message"])

                mock_send_task.assert_called_once()
                call_args = mock_send_task.call_args
                self.assertEqual(call_args[0][0], "tasks.run_feed_scroll")
                self.assertEqual(call_args[1]["args"], [JOB_ID_1])
                countdown = call_args[1]["countdown"]
                self.assertTrue(1700 <= countdown <= 1850)

            # Check DB record after resume
            async with self.session_factory() as session:
                result = await session.execute(
                    select(FeedScrollJob).where(FeedScrollJob.id == JOB_ID_1)
                )
                job = result.scalars().first()
                self.assertEqual(job.status, FeedScrollJobStatus.ACTIVE)
                self.assertIsNone(job.remaining_seconds)
                next_at = job.next_scan_at if job.next_scan_at.tzinfo else job.next_scan_at.replace(tzinfo=timezone.utc)
                diff = (next_at - datetime.now(timezone.utc)).total_seconds()
                self.assertTrue(1700 <= diff <= 1850)

        self.run_async(scenario)

    def test_pause_overdue_scan_saves_zero_and_resume_schedules_immediately(self):
        """When an overdue scan was paused, resuming schedules the first scan immediately."""
        async def scenario(client):
            # Pause overdue job
            res = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_2}/pause",
                params={"owner_email": OWNER},
            )
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["remaining_seconds"], 0)

            # Activate job
            with patch("worker.celery_app.celery_app.send_task") as mock_send_task:
                res = await client.post(
                    f"/api/v1/feed-scroll/jobs/{JOB_ID_2}/activate",
                    params={"owner_email": OWNER},
                )
                self.assertEqual(res.status_code, 200)
                self.assertIn("activated", res.json()["message"])
                mock_send_task.assert_called_once_with(
                    "tasks.run_feed_scroll", args=[JOB_ID_2], countdown=10
                )

        self.run_async(scenario)

    def test_schema_response_serialization(self):
        """Verify FeedScrollJobResponse serializes remaining_seconds."""
        now = datetime.now(timezone.utc)
        resp = FeedScrollJobResponse(
            id="job-1",
            account_email="li@test.dev",
            owner_email="owner@test.dev",
            name="Job 1",
            mode=FeedScrollMode.JOB_SEARCH,
            status=FeedScrollJobStatus.PAUSED,
            experience_min_years=None,
            experience_max_years=None,
            job_titles=["Engineer"],
            skill_set=None,
            keywords=None,
            feed_interval_hours=2,
            posts_per_scan=20,
            remaining_seconds=3600,
            last_scanned_at=now - timedelta(hours=1),
            next_scan_at=now + timedelta(hours=1),
            created_at=now,
            updated_at=now,
        )
        data = resp.model_dump()
        self.assertEqual(data["remaining_seconds"], 3600)
        self.assertEqual(data["status"], FeedScrollJobStatus.PAUSED)

    def test_dispatch_due_feed_scans_triggers_overdue_active_jobs(self):
        """dispatch_due_feed_scans finds active jobs whose next_scan_at <= now and enqueues scans."""
        import worker.tasks.feed_scroll_tasks
        with patch("worker.celery_app.celery_app.send_task") as mock_send_task:
            with patch("worker.tasks.feed_scroll_tasks.get_sync_db") as mock_sync_db:
                from contextlib import contextmanager

                @contextmanager
                def fake_db():
                    session = MagicMock()
                    now = datetime.now(timezone.utc)
                    job_overdue = FeedScrollJob(
                        id="overdue-1",
                        status=FeedScrollJobStatus.ACTIVE,
                        next_scan_at=now - timedelta(hours=2),
                    )
                    session.query().filter().all.return_value = [job_overdue]
                    yield session

                mock_sync_db.side_effect = fake_db
                from worker.tasks.feed_scroll_tasks import dispatch_due_feed_scans
                result = dispatch_due_feed_scans()
                self.assertEqual(result["scans_dispatched"], 1)
                mock_send_task.assert_called_once_with("tasks.run_feed_scroll", args=["overdue-1"])

    def test_mark_post_as_applied_and_list_applied_posts(self):
        """Marking a post as applied creates a permanent applied post record and list endpoint returns it."""
        async def scenario(client):
            # 1. Mark as applied
            res = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/results/{RESULT_ID_1}/apply",
                params={"owner_email": OWNER},
            )
            self.assertEqual(res.status_code, 200, res.text)
            data = res.json()
            self.assertEqual(data["feed_scroll_job_id"], JOB_ID_1)
            self.assertEqual(data["post_url"], "https://www.linkedin.com/feed/update/urn:li:activity:7999888777666555444/")
            self.assertEqual(data["author_name"], "Alice Recruiter")

            # 2. List applied posts for the job
            list_res = await client.get(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts",
                params={"owner_email": OWNER},
            )
            self.assertEqual(list_res.status_code, 200)
            applied_list = list_res.json()
            self.assertEqual(len(applied_list), 1)
            self.assertEqual(applied_list[0]["id"], data["id"])
            self.assertEqual(applied_list[0]["author_profile_url"], "https://www.linkedin.com/in/alicerecruiter")

            # 3. Check that results view now annotates is_applied = True
            results_res = await client.get(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/results",
                params={"owner_email": OWNER},
            )
            self.assertEqual(results_res.status_code, 200)
            results = results_res.json()
            matched_res = next((r for r in results if r["id"] == RESULT_ID_1), None)
            self.assertIsNotNone(matched_res)
            self.assertTrue(matched_res["is_applied"])
            self.assertIsNotNone(matched_res["applied_at"])

            # 4. Delete single applied post
            del_res = await client.delete(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts/{data['id']}",
                params={"owner_email": OWNER},
            )
            self.assertEqual(del_res.status_code, 200)

            # 5. List is now empty
            list_after = await client.get(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts",
                params={"owner_email": OWNER},
            )
            self.assertEqual(len(list_after.json()), 0)

        self.run_async(scenario)

    def test_bulk_delete_applied_posts(self):
        """Bulk delete endpoint removes multiple applied posts by ID."""
        async def scenario(client):
            # Create two applied posts
            p1 = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts",
                params={"owner_email": OWNER},
                json={
                    "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:111/",
                    "author_profile_url": "https://www.linkedin.com/in/user1",
                    "author_name": "User One",
                },
            )
            p2 = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts",
                params={"owner_email": OWNER},
                json={
                    "post_url": "https://www.linkedin.com/feed/update/urn:li:activity:222/",
                    "author_profile_url": "https://www.linkedin.com/in/user2",
                    "author_name": "User Two",
                },
            )
            id1 = p1.json()["id"]
            id2 = p2.json()["id"]

            # Bulk delete both
            del_res = await client.post(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts/bulk-delete",
                params={"owner_email": OWNER},
                json={"post_ids": [id1, id2]},
            )
            self.assertEqual(del_res.status_code, 200)
            self.assertEqual(del_res.json()["deleted_count"], 2)

            # Verify list is empty
            list_after = await client.get(
                f"/api/v1/feed-scroll/jobs/{JOB_ID_1}/applied-posts",
                params={"owner_email": OWNER},
            )
            self.assertEqual(len(list_after.json()), 0)

        self.run_async(scenario)


if __name__ == "__main__":
    unittest.main()

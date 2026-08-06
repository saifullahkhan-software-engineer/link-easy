"""
Tests for removing individual Feed Scroll posts from the results view.

The Feed Scroll router imports Playwright/patchright at module load, so the
package is stubbed away (the same trick the preview server uses) — only the
read/dismiss/restore endpoints are exercised, against an in-memory SQLite DB.

Covers the soft-dismiss contract:

  * dismissing a post removes it from the results list but leaves the row so
    the next scan's de-dup never brings it back
  * a dismissed post can be restored and reappears in the results list
  * dismiss is idempotent (already dismissed -> still 200)
  * a result that does not belong to the job (or the owner) is a 404
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone

_required_env = {
    "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
    "JWT_SECRET": "test-secret",
    "CREDENTIAL_ENCRYPTION_KEY": "0123456789abcdef0123456789abcdef",
    "PASSWORD_RESET_URL": "http://localhost/reset",
    "BACKEND_CORS_ORIGINS": "http://localhost:5173",
    "RESEND_API_KEY": "test",
    "FROM_EMAIL": "test@example.com",
    "REDIS_URL": "redis://localhost:6379/0",
}
for key, value in _required_env.items():
    os.environ.setdefault(key, value)

# Stub patchright before the feed-scroll router imports it at module load.
import types  # noqa: E402

_patchright = types.ModuleType("patchright")
_async_api = types.ModuleType("patchright.async_api")
for _name in ("Page", "Browser", "BrowserContext", "Locator", "ElementHandle", "TimeoutError", "Error"):
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

OWNER = "owner@test.dev"
OTHER_OWNER = "intruder@test.dev"
JOB_ID = "job-1"
RESULT_IDS = [f"res-{i}" for i in range(3)]


def _url(job_id, result_id):
    return f"/api/v1/feed-scroll/jobs/{job_id}/results/{result_id}"


class FeedScrollResultDismissTests(unittest.TestCase):
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
            session.add(FeedScrollJob(
                id=JOB_ID, account_email="li@test.dev", owner_email=OWNER,
                name="Backend hiring scan", mode=FeedScrollMode.JOB_SEARCH,
                status=FeedScrollJobStatus.ACTIVE, keywords=["hiring"],
                feed_interval_hours=1, posts_per_scan=20,
            ))
            for i, slug in enumerate(("jane-doe", "john-roe", "mary-lee")):
                session.add(FeedScrollResult(
                    id=RESULT_IDS[i], feed_scroll_job_id=JOB_ID,
                    post_urn=f"urn:li:activity:71234567890123456{i}",
                    post_url=f"https://www.linkedin.com/feed/update/urn:li:activity:71234567890123456{i}/",
                    author_name=f"{slug} x", author_first_name=slug, author_last_name="x",
                    author_profile_url=f"https://www.linkedin.com/in/{slug}",
                    post_text="We are hiring senior engineers.", score=8.0 + i,
                    matched_terms=["hiring"], scan_batch_id="batch-1",
                    scanned_at=datetime.now(timezone.utc),
                ))
            await session.commit()

    def run_async(self, coro_factory):
        async def runner():
            transport = ASGITransport(app=self.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await coro_factory(client)

        return self.loop.run_until_complete(runner())

    async def _list_ids(self, client):
        resp = await client.get(
            f"/api/v1/feed-scroll/jobs/{JOB_ID}/results",
            params={"owner_email": OWNER},
        )
        return [r["id"] for r in resp.json()]

    # ── dismiss / restore ───────────────────────────────────────────────

    def test_dismiss_removes_post_from_results_list(self):
        async def scenario(client):
            before = await self._list_ids(client)
            self.assertEqual(set(before), set(RESULT_IDS))

            dismissed = await client.delete(_url(JOB_ID, RESULT_IDS[0]), params={"owner_email": OWNER})
            self.assertEqual(dismissed.status_code, 200, dismissed.text)

            after = await self._list_ids(client)
            self.assertNotIn(RESULT_IDS[0], after)
            self.assertEqual(set(after), {RESULT_IDS[1], RESULT_IDS[2]})
            return after

        self.run_async(scenario)

    def test_dismiss_is_idempotent(self):
        async def scenario(client):
            first = await client.delete(_url(JOB_ID, RESULT_IDS[0]), params={"owner_email": OWNER})
            second = await client.delete(_url(JOB_ID, RESULT_IDS[0]), params={"owner_email": OWNER})
            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 200)

        self.run_async(scenario)

    def test_restore_brings_post_back(self):
        async def scenario(client):
            await client.delete(_url(JOB_ID, RESULT_IDS[0]), params={"owner_email": OWNER})

            restored = await client.post(
                f"{_url(JOB_ID, RESULT_IDS[0])}/restore", params={"owner_email": OWNER}
            )
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertTrue(restored.json()["restored"])

            ids = await self._list_ids(client)
            self.assertEqual(set(ids), set(RESULT_IDS))

        self.run_async(scenario)

    def test_dismiss_of_another_owners_job_is_404(self):
        async def scenario(client):
            resp = await client.delete(_url(JOB_ID, RESULT_IDS[0]), params={"owner_email": OTHER_OWNER})
            self.assertEqual(resp.status_code, 404)

        self.run_async(scenario)

    def test_dismiss_of_unknown_result_is_404(self):
        async def scenario(client):
            resp = await client.delete(_url(JOB_ID, "does-not-exist"), params={"owner_email": OWNER})
            self.assertEqual(resp.status_code, 404)

        self.run_async(scenario)

    def test_restore_of_unknown_result_is_404(self):
        async def scenario(client):
            resp = await client.post(
                f"{_url(JOB_ID, 'does-not-exist')}/restore", params={"owner_email": OWNER}
            )
            self.assertEqual(resp.status_code, 404)

        self.run_async(scenario)


if __name__ == "__main__":
    unittest.main()

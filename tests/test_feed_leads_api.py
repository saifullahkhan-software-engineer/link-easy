"""
End-to-end tests for the Feed Leads pool → campaign import pathway.

Covers the contract the Feed Scroll results view and the campaign "Feed Leads"
tab rely on:

  * saving a scanned profile stages it in the pool of its feed scroll job and
    never touches the campaign leads table
  * saving the same profile twice into one pool is a 409 (never a duplicate,
    never a silent no-op)
  * importing selected pool entries creates real campaign leads with
    ``status=pending`` and the scan metadata preserved, and consumes the pool
    entries so the pool empties as it is used
  * profiles already present in the campaign come back as duplicates instead of
    being inserted twice
  * quick-add applies the same validation as CSV import and 409s on duplicates

The app is assembled from the routers under test and pointed at an in-memory
SQLite database, so no Postgres/Redis/Playwright is required.
"""
import asyncio
import os
import unittest

# Settings are built from the environment at import time; provide placeholders
# so the suite runs in a bare source checkout.
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

import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from api.dependencies import get_db  # noqa: E402
from api.v1.campaigns import router as campaigns_router  # noqa: E402
from api.v1.feed_leads import router as feed_leads_router  # noqa: E402
from api.v1.leads import router as leads_router  # noqa: E402
from database import Base  # noqa: E402
from models.campaign import Campaign, CampaignStatus  # noqa: E402
from models.feed_lead import FeedLead, FeedLeadStatus  # noqa: E402
from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus, FeedScrollMode  # noqa: E402
from models.lead import Lead  # noqa: E402
from models.linkedin_account import LinkedInAccount, LinkedInAccountStatus  # noqa: E402
from models.user import User  # noqa: E402

OWNER = "owner@test.dev"
LI_EMAIL = "li@test.dev"
CAMPAIGN_ID = "camp-1"
JOB_ID = "job-1"
OTHER_JOB_ID = "job-2"

POST_URL = "https://www.linkedin.com/feed/update/urn:li:activity:123/"


def _profile(slug: str) -> str:
    return f"https://www.linkedin.com/in/{slug}"


def save_payload(slug="jane-doe", first="Jane", last="Doe", job_id=JOB_ID, **overrides):
    payload = {
        "owner_email": OWNER,
        "feed_scroll_job_id": job_id,
        "feed_scroll_result_id": "result-1",
        "first_name": first,
        "last_name": last,
        "linkedin_url": _profile(slug),
        "headline": "Head of Engineering at Acme",
        "source": "job_feed_scan",
        "source_post_url": POST_URL,
        "matched_score": 8.5,
        "matched_criteria": ["Software Engineer", "hiring"],
        "scan_id": "scan-1",
    }
    payload.update(overrides)
    return payload


class FeedLeadsFlowTests(unittest.TestCase):
    """Each test gets a fresh in-memory database and API client."""

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
        app.include_router(campaigns_router)
        app.include_router(leads_router)
        app.include_router(feed_leads_router)

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
            session.add(User(
                first_name="Test", last_name="Owner", email=OWNER,
                hashed_password="x", is_verified=True,
            ))
            session.add(LinkedInAccount(
                id="acct-1", owner_email=OWNER, linkedin_email=LI_EMAIL,
                encrypted_password="x", status=LinkedInAccountStatus.ACTIVE,
                profile_dir="/tmp/profiles/acct-1",
            ))
            session.add(Campaign(
                id=CAMPAIGN_ID, account_email=LI_EMAIL, name="Q3 Founders",
                status=CampaignStatus.DRAFT,
            ))
            for job_id, name in ((JOB_ID, "Backend hiring scan"), (OTHER_JOB_ID, "Design scan")):
                session.add(FeedScrollJob(
                    id=job_id, account_email=LI_EMAIL, owner_email=OWNER, name=name,
                    mode=FeedScrollMode.JOB_SEARCH, status=FeedScrollJobStatus.ACTIVE,
                    keywords=["hiring"], feed_interval_hours=1, posts_per_scan=20,
                ))
            await session.commit()

    def run_async(self, coro_factory):
        async def runner():
            transport = ASGITransport(app=self.app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await coro_factory(client)

        return self.loop.run_until_complete(runner())

    async def _count_leads(self):
        from sqlalchemy import func, select

        async with self.session_factory() as session:
            result = await session.execute(select(func.count(Lead.id)))
            return result.scalar_one()

    # ── saving into the pool ──────────────────────────────────────────────

    def test_save_stages_profile_without_creating_a_campaign_lead(self):
        async def scenario(client):
            response = await client.post("/api/v1/feed-leads", json=save_payload())
            self.assertEqual(response.status_code, 201, response.text)
            body = response.json()
            self.assertEqual(body["status"], "saved")
            self.assertEqual(body["source"], "job_feed_scan")
            self.assertEqual(body["linkedin_url"], _profile("jane-doe"))
            self.assertEqual(body["matched_criteria"], ["Software Engineer", "hiring"])
            self.assertEqual(body["scan_id"], "scan-1")
            self.assertIsNone(body["imported_campaign_id"])
            return body

        self.run_async(scenario)
        # Nothing may leak into the campaign leads table on save.
        self.assertEqual(self.loop.run_until_complete(self._count_leads()), 0)

    def test_save_normalises_url_and_rejects_non_profile_links(self):
        async def scenario(client):
            ok = await client.post(
                "/api/v1/feed-leads",
                json=save_payload(slug="jane-doe/", first="  Jane  "),
            )
            self.assertEqual(ok.status_code, 201, ok.text)
            self.assertEqual(ok.json()["linkedin_url"], _profile("jane-doe"))
            self.assertEqual(ok.json()["first_name"], "Jane")

            bad = await client.post(
                "/api/v1/feed-leads",
                json=save_payload(first="Acme", last="Corp"),
            )
            bad_url = save_payload()
            bad_url["linkedin_url"] = "https://www.linkedin.com/company/acme"
            bad = await client.post("/api/v1/feed-leads", json=bad_url)
            self.assertEqual(bad.status_code, 422, bad.text)

        self.run_async(scenario)

    def test_saving_same_profile_twice_into_one_pool_conflicts(self):
        async def scenario(client):
            first = await client.post("/api/v1/feed-leads", json=save_payload())
            self.assertEqual(first.status_code, 201)

            again = await client.post("/api/v1/feed-leads", json=save_payload())
            self.assertEqual(again.status_code, 409, again.text)
            detail = again.json()["detail"]
            self.assertEqual(detail["code"], "already_in_pool")
            self.assertEqual(detail["pool_name"], "Backend hiring scan")

            # The same profile may still be saved into a different pool.
            other_pool = await client.post(
                "/api/v1/feed-leads", json=save_payload(job_id=OTHER_JOB_ID)
            )
            self.assertEqual(other_pool.status_code, 201, other_pool.text)

        self.run_async(scenario)

    def test_pool_of_another_user_is_not_writable(self):
        async def scenario(client):
            response = await client.post(
                "/api/v1/feed-leads", json=save_payload(owner_email="intruder@test.dev")
            )
            self.assertEqual(response.status_code, 404)

        self.run_async(scenario)

    def test_pools_endpoint_reports_waiting_counts(self):
        async def scenario(client):
            await client.post("/api/v1/feed-leads", json=save_payload())
            await client.post("/api/v1/feed-leads", json=save_payload(slug="john-roe", first="John", last="Roe"))

            response = await client.get("/api/v1/feed-leads/pools", params={"owner_email": OWNER})
            self.assertEqual(response.status_code, 200, response.text)
            pools = {pool["feed_scroll_job_id"]: pool for pool in response.json()}
            self.assertEqual(pools[JOB_ID]["saved_count"], 2)
            self.assertEqual(pools[JOB_ID]["name"], "Backend hiring scan")
            self.assertEqual(pools[OTHER_JOB_ID]["saved_count"], 0)

            only_saved = await client.get(
                "/api/v1/feed-leads/pools",
                params={"owner_email": OWNER, "only_with_saved": True},
            )
            self.assertEqual([p["feed_scroll_job_id"] for p in only_saved.json()], [JOB_ID])

        self.run_async(scenario)

    def test_discarding_a_saved_profile_removes_it_from_the_pool(self):
        async def scenario(client):
            created = await client.post("/api/v1/feed-leads", json=save_payload())
            feed_lead_id = created.json()["id"]

            deleted = await client.delete(
                f"/api/v1/feed-leads/{feed_lead_id}", params={"owner_email": OWNER}
            )
            self.assertEqual(deleted.status_code, 204)

            listed = await client.get("/api/v1/feed-leads", params={"owner_email": OWNER})
            self.assertEqual(listed.json(), [])

        self.run_async(scenario)

    # ── importing the pool into a campaign ────────────────────────────────

    def test_import_creates_campaign_leads_and_consumes_the_pool(self):
        async def scenario(client):
            a = await client.post("/api/v1/feed-leads", json=save_payload())
            b = await client.post(
                "/api/v1/feed-leads", json=save_payload(slug="john-roe", first="John", last="Roe")
            )
            ids = [a.json()["id"], b.json()["id"]]

            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads",
                json={"owner_email": OWNER, "feed_lead_ids": ids},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(len(body["added"]), 2)
            self.assertEqual(body["duplicates"], [])
            self.assertEqual(body["errors"], [])
            self.assertEqual(body["campaign_name"], "Q3 Founders")

            lead = body["added"][0]
            self.assertEqual(lead["campaign_id"], CAMPAIGN_ID)
            self.assertEqual(lead["status"], "pending")
            self.assertEqual(lead["source"], "job_feed_scan")
            self.assertEqual(lead["source_post_url"], POST_URL)
            self.assertEqual(lead["matched_score"], 8.5)
            self.assertEqual(lead["matched_criteria"], ["Software Engineer", "hiring"])
            self.assertEqual(lead["scan_id"], "scan-1")
            self.assertEqual(lead["headline"], "Head of Engineering at Acme")

            # Imported leads are visible through the regular Manage Leads list.
            manage = await client.get(
                "/api/v1/leads", params={"campaign_id": CAMPAIGN_ID, "owner_email": OWNER}
            )
            self.assertEqual(len(manage.json()), 2)
            self.assertTrue(all(item["source"] == "job_feed_scan" for item in manage.json()))

            # The pool is consumed: nothing left waiting, entries marked imported.
            waiting = await client.get("/api/v1/feed-leads", params={"owner_email": OWNER})
            self.assertEqual(waiting.json(), [])

            consumed = await client.get(
                "/api/v1/feed-leads", params={"owner_email": OWNER, "status": "imported"}
            )
            self.assertEqual(len(consumed.json()), 2)
            self.assertTrue(
                all(item["imported_campaign_id"] == CAMPAIGN_ID for item in consumed.json())
            )
            self.assertTrue(all(item["imported_lead_id"] for item in consumed.json()))

        self.run_async(scenario)

    def test_import_reports_profiles_already_in_the_campaign(self):
        async def scenario(client):
            # Already a lead of this campaign (as if added manually / via CSV).
            manual = await client.post(
                "/api/v1/leads",
                json={
                    "owner_email": OWNER,
                    "campaign_id": CAMPAIGN_ID,
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "linkedin_url": _profile("jane-doe"),
                },
            )
            self.assertEqual(manual.status_code, 201, manual.text)

            saved = await client.post("/api/v1/feed-leads", json=save_payload())
            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads",
                json={"owner_email": OWNER, "feed_lead_ids": [saved.json()["id"]]},
            )
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["added"], [])
            self.assertEqual(len(body["duplicates"]), 1)
            self.assertEqual(body["duplicates"][0]["reason"], "duplicate")
            self.assertIn("Q3 Founders", body["duplicates"][0]["message"])

        self.run_async(scenario)
        # Exactly one lead: the manual one. No duplicate row was inserted.
        self.assertEqual(self.loop.run_until_complete(self._count_leads()), 1)

    def test_import_is_idempotent_for_already_consumed_entries(self):
        async def scenario(client):
            saved = await client.post("/api/v1/feed-leads", json=save_payload())
            feed_lead_id = saved.json()["id"]
            payload = {"owner_email": OWNER, "feed_lead_ids": [feed_lead_id]}

            first = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads", json=payload
            )
            self.assertEqual(len(first.json()["added"]), 1)

            second = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads", json=payload
            )
            self.assertEqual(second.json()["added"], [])
            self.assertEqual(len(second.json()["duplicates"]), 1)

        self.run_async(scenario)
        self.assertEqual(self.loop.run_until_complete(self._count_leads()), 1)

    def test_import_rejects_a_campaign_owned_by_someone_else(self):
        async def scenario(client):
            saved = await client.post("/api/v1/feed-leads", json=save_payload())
            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads",
                json={"owner_email": "intruder@test.dev", "feed_lead_ids": [saved.json()["id"]]},
            )
            self.assertEqual(response.status_code, 404)

        self.run_async(scenario)

    def test_import_flags_unknown_entries_without_failing_the_batch(self):
        async def scenario(client):
            saved = await client.post("/api/v1/feed-leads", json=save_payload())
            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/import-feed-leads",
                json={"owner_email": OWNER, "feed_lead_ids": [saved.json()["id"], "does-not-exist"]},
            )
            body = response.json()
            self.assertEqual(len(body["added"]), 1)
            self.assertEqual(len(body["errors"]), 1)
            self.assertEqual(body["errors"][0]["reason"], "not_found")

        self.run_async(scenario)

    # ── quick-add ─────────────────────────────────────────────────────────

    def test_quick_add_inserts_a_lead_with_source_metadata(self):
        async def scenario(client):
            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/quick-add",
                json={
                    "owner_email": OWNER,
                    "first_name": "Jane",
                    "last_name": "Doe",
                    "linkedin_url": _profile("jane-doe"),
                    "source": "job_feed_scan",
                    "source_post_url": POST_URL,
                    "matched_score": 7.25,
                    "matched_criteria": ["Python"],
                    "scan_id": "scan-9",
                },
            )
            self.assertEqual(response.status_code, 201, response.text)
            lead = response.json()
            self.assertEqual(lead["status"], "pending")
            self.assertEqual(lead["current_step"], 1)
            self.assertEqual(lead["source"], "job_feed_scan")
            self.assertEqual(lead["matched_score"], 7.25)

        self.run_async(scenario)

    def test_quick_add_conflicts_on_duplicate_profile(self):
        async def scenario(client):
            payload = {
                "owner_email": OWNER,
                "first_name": "Jane",
                "last_name": "Doe",
                "linkedin_url": _profile("jane-doe"),
            }
            first = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/quick-add", json=payload
            )
            self.assertEqual(first.status_code, 201)

            again = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/quick-add", json=payload
            )
            self.assertEqual(again.status_code, 409, again.text)
            detail = again.json()["detail"]
            self.assertEqual(detail["code"], "already_in_campaign")
            self.assertEqual(detail["campaign_name"], "Q3 Founders")
            self.assertIn("Q3 Founders", detail["message"])

        self.run_async(scenario)
        self.assertEqual(self.loop.run_until_complete(self._count_leads()), 1)

    def test_quick_add_uses_the_csv_url_validation_rules(self):
        async def scenario(client):
            response = await client.post(
                f"/api/v1/campaigns/{CAMPAIGN_ID}/leads/quick-add",
                json={
                    "owner_email": OWNER,
                    "first_name": "Acme",
                    "last_name": "Corp",
                    "linkedin_url": "https://www.linkedin.com/company/acme",
                },
            )
            self.assertEqual(response.status_code, 422, response.text)

        self.run_async(scenario)


class SharedLeadValidationTests(unittest.TestCase):
    """The one validator every intake path (CSV, manual, pool, quick-add) uses."""

    def test_requires_all_identity_fields(self):
        from schemas.lead import validate_lead_fields

        with self.assertRaises(ValueError) as ctx:
            validate_lead_fields("", "Doe", _profile("jane"))
        self.assertIn("first_name", str(ctx.exception))

        with self.assertRaises(ValueError):
            validate_lead_fields("Jane", "", _profile("jane"))
        with self.assertRaises(ValueError):
            validate_lead_fields("Jane", "Doe", "")

    def test_normalises_values(self):
        from schemas.lead import validate_lead_fields

        cleaned = validate_lead_fields("  Jane ", " Doe ", "  https://www.linkedin.com/in/jane/  ")
        self.assertEqual(cleaned, {
            "first_name": "Jane",
            "last_name": "Doe",
            "linkedin_url": "https://www.linkedin.com/in/jane",
        })

    def test_rejects_non_profile_urls(self):
        from schemas.lead import validate_lead_fields

        for url in (
            "https://www.linkedin.com/company/acme",
            "http://www.linkedin.com/in/jane",
            "linkedin.com/in/jane",
        ):
            with self.assertRaises(ValueError, msg=url):
                validate_lead_fields("Jane", "Doe", url)


if __name__ == "__main__":
    unittest.main()

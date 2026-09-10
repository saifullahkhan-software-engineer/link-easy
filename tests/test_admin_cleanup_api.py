"""Tests for the admin cleanup endpoints (closed sessions / jobs / stale tasks).

Contract under test:

* Deleting WhatsApp sessions NEVER deletes a WhatsAppScanFilter — attached
  filters are detached (session_id → NULL) and stay with their login account.
* Connected sessions are never matched by the closed-session cleanup.
* Campaign-job deletion works in any state and revokes the Celery task.
* Stale-task preview/cleanup only reports reserved/scheduled work whose
  campaign, feed scan, or filter is no longer active — across all users.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("PASSWORD_RESET_URL", "http://localhost/reset")
os.environ.setdefault("BACKEND_CORS_ORIGINS", "http://localhost")
os.environ.setdefault("RESEND_API_KEY", "test")
os.environ.setdefault("FROM_EMAIL", "test@example.com")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", "a" * 64)
os.environ.setdefault("JWT_SECRET", "test")

from fastapi import HTTPException  # noqa: E402
from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from database import Base  # noqa: E402
from models.campaign import Campaign, CampaignStatus  # noqa: E402
from models.campaign_job import CampaignJob, JobStatus  # noqa: E402
from models.linkedin_account import LinkedInAccount  # noqa: E402
from models.user import User  # noqa: E402
from models.whatsapp import WhatsAppScanFilter, WhatsAppSession  # noqa: E402
from schemas.admin import (  # noqa: E402
    ClosedSessionsCleanupRequest,
    LinkedInJobsBulkDeleteRequest,
    StaleCleanupRequest,
)


async def _make_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(db):
    db.add(
        User(
            first_name="Admin",
            last_name="User",
            email="admin@test.dev",
            hashed_password="x",
            is_verified=True,
            role="admin",
        )
    )
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
        LinkedInAccount(
            owner_email="owner@test.dev",
            linkedin_email="li@test.dev",
            encrypted_password="enc",
            profile_dir="/tmp/profiles/li",
            status="active",
        )
    )
    # 1 connected session + 3 closed ones (disconnected / error / waiting_qr).
    db.add(WhatsAppSession(owner_email="owner@test.dev", status="connected", is_active=True))
    db.add(WhatsAppSession(owner_email="owner@test.dev", status="disconnected", is_active=False))
    db.add(WhatsAppSession(owner_email="owner@test.dev", status="error", is_active=False))
    db.add(WhatsAppSession(owner_email="owner@test.dev", status="waiting_qr", is_active=True))
    db.add(
        Campaign(
            id="camp-1",
            account_email="li@test.dev",
            name="Q3 Founders",
            status=CampaignStatus.ACTIVE,
        )
    )
    db.add(
        CampaignJob(
            id="job-queued",
            campaign_id="camp-1",
            lead_id="lead-1",
            step_type="send_connection",
            status=JobStatus.QUEUED,
            celery_task_id="celery-1",
        )
    )
    db.add(
        CampaignJob(
            id="job-running",
            campaign_id="camp-1",
            lead_id="lead-1",
            step_type="send_message",
            status=JobStatus.RUNNING,
        )
    )
    db.add(
        CampaignJob(
            id="job-done",
            campaign_id="camp-1",
            lead_id="lead-1",
            step_type="visit_profile",
            status=JobStatus.DONE,
        )
    )
    db.add(
        CampaignJob(
            id="job-failed",
            campaign_id="camp-1",
            lead_id="lead-1",
            step_type="send_connection",
            status=JobStatus.FAILED,
        )
    )
    await db.commit()

    sessions = (await db.execute(select(WhatsAppSession).order_by(WhatsAppSession.id))).scalars().all()
    by_status = {s.status: s for s in sessions}
    # One filter on the connected device, one on a closed device, one paused.
    db.add(
        WhatsAppScanFilter(
            name="On live device",
            status="active",
            owner_email="owner@test.dev",
            session_id=by_status["connected"].id,
        )
    )
    db.add(
        WhatsAppScanFilter(
            name="On closed device",
            status="active",
            owner_email="owner@test.dev",
            session_id=by_status["disconnected"].id,
        )
    )
    db.add(
        WhatsAppScanFilter(
            name="Paused filter",
            status="paused",
            owner_email="owner@test.dev",
        )
    )
    await db.commit()
    return by_status


def _admin():
    return SimpleNamespace(email="admin@test.dev")


class AdminCleanupApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.Session = await _make_session()
        async with self.Session() as db:
            self.session_by_status = await _seed(db)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # ── Closed sessions ──────────────────────────────────────────────────

    async def test_cleanup_closed_dry_run_matches_only_closed(self):
        from api.v1.admin import admin_cleanup_closed_whatsapp_sessions

        async with self.Session() as db:
            response = await admin_cleanup_closed_whatsapp_sessions(
                ClosedSessionsCleanupRequest(dry_run=True, limit=500),
                _admin(),
                db,
            )

        self.assertTrue(response.dry_run)
        self.assertEqual(response.matched, 3)
        self.assertEqual(response.deleted, 0)
        self.assertEqual(
            {item.status for item in response.sessions},
            {"disconnected", "error", "waiting_qr"},
        )
        async with self.Session() as db:
            remaining = await db.scalar(select(func.count(WhatsAppSession.id)))
        self.assertEqual(remaining, 4)

    async def test_cleanup_closed_deletes_and_preserves_filters(self):
        from api.v1.admin import admin_cleanup_closed_whatsapp_sessions

        async with self.Session() as db:
            with (
                patch("api.v1.admin._revoke_tasks_for_filters", return_value=[]),
                patch("api.v1.admin._delete_whatsapp_leases", return_value=0),
            ):
                response = await admin_cleanup_closed_whatsapp_sessions(
                    ClosedSessionsCleanupRequest(dry_run=False, limit=500),
                    _admin(),
                    db,
                )

        self.assertEqual(response.deleted, 3)
        self.assertEqual(response.preserved_filters, 1)

        async with self.Session() as db:
            remaining = (await db.execute(select(WhatsAppSession))).scalars().all()
            self.assertEqual([s.status for s in remaining], ["connected"])

            # Both filters still exist: the one on the closed device was
            # detached (kept, session_id NULL), the live one untouched.
            filters = (
                (await db.execute(select(WhatsAppScanFilter).order_by(WhatsAppScanFilter.id)))
                .scalars()
                .all()
            )
            self.assertEqual(len(filters), 3)
            by_name = {f.name: f for f in filters}
            self.assertIsNone(by_name["On closed device"].session_id)
            self.assertEqual(by_name["On closed device"].owner_email, "owner@test.dev")
            self.assertEqual(
                by_name["On live device"].session_id, remaining[0].id
            )

    async def test_cleanup_closed_rejects_connected_status(self):
        from api.v1.admin import admin_cleanup_closed_whatsapp_sessions

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_cleanup_closed_whatsapp_sessions(
                    ClosedSessionsCleanupRequest(statuses=["connected"]),
                    _admin(),
                    db,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_cleanup_closed_rejects_unknown_status(self):
        from api.v1.admin import admin_cleanup_closed_whatsapp_sessions

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_cleanup_closed_whatsapp_sessions(
                    ClosedSessionsCleanupRequest(statuses=["nope"]),
                    _admin(),
                    db,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_cleanup_closed_session_ids_skip_connected(self):
        from api.v1.admin import admin_cleanup_closed_whatsapp_sessions

        connected_id = self.session_by_status["connected"].id
        async with self.Session() as db:
            with (
                patch("api.v1.admin._revoke_tasks_for_filters", return_value=[]),
                patch("api.v1.admin._delete_whatsapp_leases", return_value=0),
            ):
                response = await admin_cleanup_closed_whatsapp_sessions(
                    ClosedSessionsCleanupRequest(session_ids=[connected_id]),
                    _admin(),
                    db,
                )

        self.assertEqual(response.matched, 0)
        self.assertEqual(response.deleted, 0)
        self.assertEqual(len(response.skipped), 1)
        self.assertEqual(response.skipped[0]["id"], connected_id)

    async def test_delete_single_session_detaches_filters(self):
        from api.v1.admin import admin_delete_whatsapp_session

        target = self.session_by_status["disconnected"].id
        async with self.Session() as db:
            with (
                patch("api.v1.admin._revoke_tasks_for_filters", return_value=[]),
                patch("api.v1.admin._delete_whatsapp_leases", return_value=0),
            ):
                response = await admin_delete_whatsapp_session(target, _admin(), db)

        self.assertEqual(response["deleted"], target)
        self.assertEqual(response["detached_filters"], 1)

        async with self.Session() as db:
            gone = await db.scalar(
                select(WhatsAppSession).where(WhatsAppSession.id == target)
            )
            self.assertIsNone(gone)
            kept = await db.scalar(
                select(WhatsAppScanFilter).where(
                    WhatsAppScanFilter.name == "On closed device"
                )
            )
            self.assertIsNotNone(kept)
            self.assertIsNone(kept.session_id)

    async def test_delete_single_session_404(self):
        from api.v1.admin import admin_delete_whatsapp_session

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_delete_whatsapp_session(999999, _admin(), db)
        self.assertEqual(ctx.exception.status_code, 404)

    # ── LinkedIn jobs ────────────────────────────────────────────────────

    async def test_delete_linkedin_job_revokes_task(self):
        from api.v1.admin import admin_delete_linkedin_job

        async with self.Session() as db:
            with patch(
                "api.v1.admin._best_effort_revoke", return_value=["celery-1"]
            ) as revoke:
                response = await admin_delete_linkedin_job("job-queued", _admin(), db)

        self.assertEqual(response["deleted"], "job-queued")
        revoke.assert_called_once_with(["celery-1"])
        async with self.Session() as db:
            gone = await db.scalar(
                select(CampaignJob).where(CampaignJob.id == "job-queued")
            )
            self.assertIsNone(gone)

    async def test_delete_linkedin_job_404(self):
        from api.v1.admin import admin_delete_linkedin_job

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_delete_linkedin_job("missing", _admin(), db)
        self.assertEqual(ctx.exception.status_code, 404)

    async def test_bulk_delete_linkedin_jobs_by_status(self):
        from api.v1.admin import admin_bulk_delete_linkedin_jobs

        async with self.Session() as db:
            preview = await admin_bulk_delete_linkedin_jobs(
                LinkedInJobsBulkDeleteRequest(
                    statuses=["queued", "running"], dry_run=True, limit=100
                ),
                _admin(),
                db,
            )
        self.assertTrue(preview.dry_run)
        self.assertEqual(preview.matched, 2)

        async with self.Session() as db:
            with patch(
                "api.v1.admin._best_effort_revoke", return_value=["celery-1"]
            ):
                done = await admin_bulk_delete_linkedin_jobs(
                    LinkedInJobsBulkDeleteRequest(
                        statuses=["queued", "running"], dry_run=False, limit=100
                    ),
                    _admin(),
                    db,
                )
        self.assertEqual(done.deleted, 2)
        self.assertEqual(done.revoked_tasks, 1)

        async with self.Session() as db:
            remaining = (
                (await db.execute(select(CampaignJob.id))).scalars().all()
            )
            self.assertEqual(sorted(remaining), ["job-done", "job-failed"])

    async def test_bulk_delete_linkedin_jobs_rejects_unknown_status(self):
        from api.v1.admin import admin_bulk_delete_linkedin_jobs

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_bulk_delete_linkedin_jobs(
                    LinkedInJobsBulkDeleteRequest(statuses=["bogus"]),
                    _admin(),
                    db,
                )
        self.assertEqual(ctx.exception.status_code, 400)

    # ── Stale tasks ──────────────────────────────────────────────────────

    def _canned_inspect(self, paused_filter_id):
        return {
            "raw": {
                "active": {"worker1": []},
                "scheduled": {
                    "worker1": [
                        {"request": {"id": "t-live", "name": "tasks.run_account_session", "args": ["li@test.dev"]}},
                        {"request": {"id": "t-ghost", "name": "tasks.run_account_session", "args": ["ghost@test.dev"]}},
                        {"request": {"id": "t-paused", "name": "tasks.check_whatsapp_messages", "args": [paused_filter_id]}},
                        {"request": {"id": "t-feed", "name": "tasks.run_feed_scroll", "args": ["feed-999"]}},
                        {"request": {"id": "t-legacy", "name": "tasks.execute_campaign_step", "args": []}},
                    ]
                },
                "reserved": {},
            },
            "workers": ["worker1"],
        }

    async def test_stale_preview_classifies_globally(self):
        from api.v1.admin import admin_stale_preview

        async with self.Session() as db:
            paused_id = await db.scalar(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.name == "Paused filter"
                )
            )
            with patch(
                "api.v1.admin._celery_inspect",
                return_value=self._canned_inspect(paused_id),
            ):
                response = await admin_stale_preview("all", _admin(), db)

        self.assertEqual(response.inspected, 5)
        # The live account session is fine; ghost account, paused filter,
        # unknown feed job, and legacy task are stale.
        self.assertEqual(response.stale_count, 4)
        self.assertEqual(
            {task.id for task in response.stale},
            {"t-ghost", "t-paused", "t-feed", "t-legacy"},
        )
        self.assertTrue(all(task.reason for task in response.stale))

    async def test_stale_preview_scope_filter(self):
        from api.v1.admin import admin_stale_preview

        async with self.Session() as db:
            paused_id = await db.scalar(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.name == "Paused filter"
                )
            )
            with patch(
                "api.v1.admin._celery_inspect",
                return_value=self._canned_inspect(paused_id),
            ):
                response = await admin_stale_preview("whatsapp", _admin(), db)

        self.assertEqual(response.stale_count, 1)
        self.assertEqual(response.stale[0].id, "t-paused")

    async def test_stale_preview_rejects_unknown_scope(self):
        from api.v1.admin import admin_stale_preview

        async with self.Session() as db:
            with self.assertRaises(HTTPException) as ctx:
                await admin_stale_preview("bogus", _admin(), db)
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_stale_cleanup_dry_run_by_default(self):
        from api.v1.admin import admin_cleanup_stale_tasks

        async with self.Session() as db:
            paused_id = await db.scalar(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.name == "Paused filter"
                )
            )
            with patch(
                "api.v1.admin._celery_inspect",
                return_value=self._canned_inspect(paused_id),
            ):
                response = await admin_cleanup_stale_tasks(
                    StaleCleanupRequest(scope="all", limit=50), _admin(), db
                )

        self.assertTrue(response.dry_run)
        self.assertEqual(response.revoked_count, 0)
        self.assertEqual(len(response.revoked), 4)

    async def test_stale_cleanup_revokes_when_confirmed(self):
        from api.v1.admin import admin_cleanup_stale_tasks

        async with self.Session() as db:
            paused_id = await db.scalar(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.name == "Paused filter"
                )
            )
            with (
                patch(
                    "api.v1.admin._celery_inspect",
                    return_value=self._canned_inspect(paused_id),
                ),
                patch(
                    "api.v1.admin._best_effort_revoke",
                    side_effect=lambda ids, **kwargs: list(ids),
                ),
            ):
                response = await admin_cleanup_stale_tasks(
                    StaleCleanupRequest(scope="all", limit=50, dry_run=False),
                    _admin(),
                    db,
                )

        self.assertFalse(response.dry_run)
        self.assertEqual(response.revoked_count, 4)


if __name__ == "__main__":
    unittest.main()

"""
Celery tasks for feed scroll automation.
FILE: worker/tasks/feed_scroll_tasks.py

Periodically scans the LinkedIn feed, scores posts, and stores results.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.logging_config import get_logger
from worker.celery_app import celery_app
from worker.playwright_semaphore import acquire_playwright_session
from worker.profile_lock import acquire_profile_lock, release_profile_lock
from automation.browser import launch_persistent_browser
from automation.session import verify_session, LinkedInSessionStatus
from automation.actions.feed_scroll import scroll_feed_and_collect
from automation.scoring.feed_scorer import score_post
from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus
from models.feed_scroll_result import FeedScrollResult

logger = get_logger(__name__)


# ── Sync DB session for Celery ──
_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
_engine = create_engine(_sync_url, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_engine)


def get_sync_db():
    """Context manager for a sync SQLAlchemy session in Celery tasks."""
    from contextlib import contextmanager

    @contextmanager
    def _session():
        session = SyncSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return _session()


@celery_app.task(bind=True, name="tasks.run_feed_scroll")
def run_feed_scroll(self, feed_scroll_job_id: str):
    """
    Execute a feed scroll scan for a job.

    1. Acquire playwright session and profile lock
    2. Launch browser and verify LinkedIn session
    3. Scroll feed and collect posts
    4. Score each post against job criteria
    5. Store top N results in DB
    6. Schedule next scan based on interval
    """
    logger.info(f"Starting feed scroll for job {feed_scroll_job_id}")

    with get_sync_db() as db:
        # Fetch the job
        job = db.query(FeedScrollJob).filter(FeedScrollJob.id == feed_scroll_job_id).first()
        if not job:
            logger.error(f"Feed scroll job {feed_scroll_job_id} not found")
            return

        if job.status != FeedScrollJobStatus.ACTIVE:
            logger.info(f"Job {feed_scroll_job_id} is not active (status={job.status}), skipping")
            return

        account_email = job.account_email
        posts_per_scan = job.posts_per_scan

    # Run the async browser automation
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(
            _run_feed_scroll_async(account_email, posts_per_scan, job)
        )
        loop.close()
    except Exception as e:
        logger.error(f"Feed scroll failed for job {feed_scroll_job_id}: {e}")
        results = {"posts": [], "error": str(e)}

    # Store results in DB
    with get_sync_db() as db:
        job = db.query(FeedScrollJob).filter(FeedScrollJob.id == feed_scroll_job_id).first()
        if not job:
            return

        scan_batch_id = str(uuid.uuid4())
        scan_time = datetime.now(timezone.utc)

        # Score and store posts
        scored_posts = []
        config = {
            "mode": job.mode.value,
            "job_titles": job.job_titles or [],
            "skill_set": job.skill_set or [],
            "experience_min_years": job.experience_min_years,
            "experience_max_years": job.experience_max_years,
            "keywords": job.keywords or [],
        }

        for post_data in results.get("posts", []):
            score, matched_terms = score_post(post_data.get("post_text", ""), config)
            scored_posts.append({
                **post_data,
                "score": score,
                "matched_terms": matched_terms,
            })

        # Sort by score and take top N
        scored_posts.sort(key=lambda x: x["score"], reverse=True)
        top_posts = scored_posts[:posts_per_scan]

        # Store results
        for post_data in top_posts:
            result = FeedScrollResult(
                id=str(uuid.uuid4()),
                feed_scroll_job_id=job.id,
                post_urn=post_data.get("post_urn"),
                post_url=post_data.get("post_url"),
                author_name=post_data.get("author_name"),
                post_text=post_data.get("post_text"),
                score=post_data["score"],
                matched_terms=post_data["matched_terms"],
                scan_batch_id=scan_batch_id,
                scanned_at=scan_time,
            )
            db.add(result)

        # Update job timestamps
        job.last_scanned_at = scan_time
        next_scan = scan_time + timedelta(hours=job.feed_interval_hours)
        job.next_scan_at = next_scan

        logger.info(
            f"Feed scan complete for job {feed_scroll_job_id}: "
            f"{len(top_posts)} posts stored, top score {top_posts[0]['score'] if top_posts else 0}"
        )

        # Schedule next scan
        if job.status == FeedScrollJobStatus.ACTIVE:
            _schedule_next_scan(job.id, job.feed_interval_hours)


async def _run_feed_scroll_async(account_email: str, posts_per_scan: int, job) -> dict:
    """Async wrapper for browser automation."""
    from models.linkedin_account import LinkedInAccount

    with get_sync_db() as db:
        account = (
            db.query(LinkedInAccount)
            .filter(LinkedInAccount.linkedin_email == account_email)
            .first()
        )
        if not account:
            return {"posts": [], "error": "LinkedIn account not found"}

    # Acquire locks
    async with acquire_playwright_session():
        lock = await acquire_profile_lock(account_email, timeout_seconds=60)
        try:
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

            try:
                # Verify LinkedIn session
                session_status = await verify_session(page)
                if session_status != LinkedInSessionStatus.VALID:
                    return {"posts": [], "error": f"Invalid session: {session_status}"}

                # Scroll feed and collect posts (collect more than we need to have better scoring)
                posts = await scroll_feed_and_collect(
                    page, target_posts=max(posts_per_scan * 3, 30), max_scrolls=15
                )

                return {"posts": posts}

            finally:
                await context.close()
                await pw.stop()

        finally:
            await release_profile_lock(lock)


def _schedule_next_scan(job_id: str, interval_hours: int):
    """Schedule the next feed scan."""
    import random

    # Add jitter: ±15 minutes
    jitter_seconds = random.randint(-900, 900)
    delay_seconds = interval_hours * 3600 + jitter_seconds

    celery_app.send_task(
        "tasks.run_feed_scroll",
        args=[job_id],
        countdown=delay_seconds,
    )

    logger.info(f"Scheduled next scan for job {job_id} in {interval_hours} hours (±15 min)")

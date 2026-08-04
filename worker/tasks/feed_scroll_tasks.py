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
from core.logging_config import should_take_screenshots
from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus

# Force diagnostic screenshots during feed scroll scans (very helpful for debugging)
FORCE_FEED_SCREENSHOTS = True


def _should_screenshot() -> bool:
    """Force screenshots for feed scroll debugging."""
    return FORCE_FEED_SCREENSHOTS or should_take_screenshots()
from models.feed_scroll_result import FeedScrollResult

logger = get_logger(__name__)


# ── Sync DB session for Celery ──
# expire_on_commit=False: objects stay usable after the session commits/closes
# (they keep their loaded state instead of being marked stale and requiring a
# refresh). The async app session in database.py already uses this; without it,
# a committed-then-closed session leaves ORM instances detached AND expired,
# and any attribute access raises DetachedInstanceError.
_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
_engine = create_engine(_sync_url, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_engine, expire_on_commit=False)


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
    logger.info(f"🚀 Starting feed scroll for job {feed_scroll_job_id}")

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
        raw_posts = results.get("posts", [])
        logger.info(f"📊 Scoring {len(raw_posts)} collected posts for job {feed_scroll_job_id}...")

        scored_posts = []
        config = {
            "mode": job.mode.value,
            "job_titles": job.job_titles or [],
            "skill_set": job.skill_set or [],
            "experience_min_years": job.experience_min_years,
            "experience_max_years": job.experience_max_years,
            "keywords": job.keywords or [],
        }

        for post_data in raw_posts:
            score, matched_terms = score_post(post_data.get("post_text", ""), config)
            scored_posts.append({
                **post_data,
                "score": score,
                "matched_terms": matched_terms,
            })

        # Sort by score and take top N
        scored_posts.sort(key=lambda x: x["score"], reverse=True)
        top_posts = scored_posts[:posts_per_scan]

        logger.info(
            f"🏆 Top {len(top_posts)} posts after scoring (from {len(raw_posts)} raw). "
            f"Highest score: {top_posts[0]['score'] if top_posts else 0}"
        )

        if len(top_posts) == 0 and raw_posts:
            logger.warning("⚠️ Had raw posts but none survived top-N cut or scoring")
        elif len(top_posts) == 0:
            logger.error("❌ No posts were collected at all for this scan")

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
            f"✅ Feed scan complete for job {feed_scroll_job_id}: "
            f"{len(top_posts)} posts stored | top score {top_posts[0]['score'] if top_posts else 0} | batch={scan_batch_id}"
        )

        # Schedule next scan
        if job.status == FeedScrollJobStatus.ACTIVE:
            _schedule_next_scan(job.id, job.feed_interval_hours)


async def _run_feed_scroll_async(account_email: str, posts_per_scan: int, job) -> dict:
    """Async wrapper for browser automation."""
    from models.linkedin_account import LinkedInAccount

    # IMPORTANT: the `account` ORM instance must stay bound to its DB session
    # while its attributes are read and the browser is launched.
    # `launch_persistent_browser()` reads account.* (profile_dir, proxy_*,
    # pinned fingerprint...) and on the first-ever launch MUTATES the row to
    # pin the fingerprint — so the launch happens INSIDE the `with` block and
    # the commit on block-exit persists any freshly pinned fingerprint.
    #
    # (Before this fix the account was queried in a short-lived session, then
    # that session was committed+closed, leaving the account detached AND
    # expired — the next attribute access raised:
    #   DetachedInstanceError: Instance <LinkedInAccount> is not bound to a
    #   Session; attribute refresh operation cannot proceed
    # ...and every feed scan failed before the browser even launched.)
    with get_sync_db() as db:
        account = (
            db.query(LinkedInAccount)
            .filter(LinkedInAccount.linkedin_email == account_email)
            .first()
        )
        if not account:
            return {"posts": [], "error": "LinkedIn account not found"}

        # Acquire locks
        # acquire_playwright_session is a synchronous context manager because the
        # semaphore uses the synchronous Redis client.  This task runs the browser
        # in an asyncio loop, but the Redis guard must still be entered with `with`.
        with acquire_playwright_session() as acquired:
            if not acquired:
                return {"posts": [], "error": "Timed out waiting for a Playwright session slot"}

            lock = acquire_profile_lock(account.id, blocking_timeout=60)
            try:
                pw, _, context, page = await launch_persistent_browser(account, headless=True)

                try:
                    # Verify LinkedIn session
                    logger.info(f"🔐 Verifying LinkedIn session for {account_email}...")
                    session_status = await verify_session(page)
                    if session_status.status != LinkedInSessionStatus.VALID:
                        logger.error(f"❌ Invalid session for feed scan: {session_status}")
                        if _should_screenshot():
                            try:
                                await page.screenshot(path="feed_session_invalid.png", full_page=True)
                                logger.info("📸 Saved feed_session_invalid.png")
                            except Exception:
                                pass
                        return {"posts": [], "error": f"Invalid session: {session_status}"}

                    logger.info("✅ Session verified. Starting feed collection...")

                    # Extra diagnostics
                    try:
                        current_url = page.url
                        page_title = await page.title()
                        logger.info(f"📍 Current page: {current_url} | Title: {page_title[:80]}")
                    except Exception:
                        pass

                    # Scroll feed and collect posts (collect more than we need to have better scoring)
                    posts = await scroll_feed_and_collect(
                        page, target_posts=max(posts_per_scan * 3, 30), max_scrolls=15
                    )

                    # === NEW: Save final screenshot after collection (very useful for debugging) ===
                    if _should_screenshot():
                        try:
                            await page.screenshot(path="feed_after_scroll_complete.png", full_page=True)
                            logger.info("📸 Saved feed_after_scroll_complete.png")
                        except Exception:
                            pass

                    if len(posts) == 0:
                        logger.warning("⚠️ scroll_feed_and_collect returned ZERO posts!")
                    else:
                        logger.info(f"✅ Collected {len(posts)} raw posts from feed before scoring")

                    return {"posts": posts}

                finally:
                    await context.close()
                    await pw.stop()

            finally:
                release_profile_lock(lock)


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

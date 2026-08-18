"""
Celery tasks for feed scroll automation.
FILE: worker/tasks/feed_scroll_tasks.py

Periodically scans the LinkedIn feed, scores posts, and stores results.
"""
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.logging_config import get_logger
from worker.celery_app import celery_app
from worker.playwright_semaphore import acquire_playwright_session
from worker.profile_lock import acquire_profile_lock, release_profile_lock
from worker.dispatch_lease import claim_dispatch_lease, release_dispatch_lease
from automation.browser import launch_persistent_browser
from automation.session import verify_session, LinkedInSessionStatus
from automation.actions.feed_scroll import (
    scroll_feed_and_collect,
    _post_identity_key,
    _resolve_result_urls,
)
from automation.scoring.feed_scorer import score_post
from core.logging_config import should_log_debug, should_take_screenshots
from models.feed_scroll_job import (
    DEFAULT_POSTS_PER_SCAN,
    MAX_POSTS_PER_SCAN,
    FeedScrollJob,
    FeedScrollJobStatus,
)
from models.feed_scroll_result import FeedScrollResult
from models.feed_scroll_applied_post import FeedScrollAppliedPost

# Force diagnostic screenshots during feed scroll scans (very helpful for debugging)
FORCE_FEED_SCREENSHOTS = True

# Every scan looks for 20-30 posts in the feed...
COLLECT_MIN_POSTS = 20
COLLECT_MAX_POSTS = 30
# ...but only the top twenty scored posts are ever kept per job.
MAX_POSTS_KEPT = MAX_POSTS_PER_SCAN


def _should_screenshot() -> bool:
    """Force screenshots for feed scroll debugging."""
    return FORCE_FEED_SCREENSHOTS or should_take_screenshots()


logger = get_logger(__name__)


# ── Sync DB session for Celery ──
# expire_on_commit=False: objects stay usable after the session commits/closes
# (they keep their loaded state instead of being marked stale and requiring a
# refresh). The async app session in database.py already uses this; without it,
# a committed-then-closed session leaves ORM instances detached AND expired,
# and any attribute access raises DetachedInstanceError.
def _make_sync_url(async_url: str) -> str:
    url = async_url
    for prefix in (
        "postgresql+asyncpg://",
        "postgres+asyncpg://",
    ):
        if url.startswith(prefix):
            url = url.replace(prefix, "postgresql+psycopg2://", 1)
            return url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


_sync_url = _make_sync_url(settings.DATABASE_URL)
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
def run_feed_scroll(
    self,
    feed_scroll_job_id: str,
    dispatch_token: str | None = None,
    force: bool = False,
):
    """Execute one feed scan.

    Beat dispatches with a lease token and the task verifies the durable
    ``next_scan_at`` before opening LinkedIn.  ``force`` is reserved for an
    explicit manual scan.  This makes an old queued task harmless after a job
    is paused, deleted, or rescheduled.
    """
    import redis

    dispatch_key = f"linkeasy:scheduler:feed:{feed_scroll_job_id}"
    redis_client = redis.from_url(settings.REDIS_URL)
    logger.info(f"🚀 Starting feed scroll for job {feed_scroll_job_id}")

    with get_sync_db() as db:
        # Fetch the job
        job = db.query(FeedScrollJob).filter(FeedScrollJob.id == feed_scroll_job_id).first()
        if not job:
            logger.info(f"⏭️ Feed scroll job {feed_scroll_job_id} no longer exists; stale task ignored")
            release_dispatch_lease(redis_client, dispatch_key, dispatch_token)
            return {"status": "skipped", "reason": "job_not_found"}

        if job.status != FeedScrollJobStatus.ACTIVE:
            logger.info(f"⏭️ Job {feed_scroll_job_id} is not active (status={job.status}), stale task ignored")
            release_dispatch_lease(redis_client, dispatch_key, dispatch_token)
            return {"status": "skipped", "reason": "job_not_active"}

        now = datetime.now(timezone.utc)
        next_scan_at = job.next_scan_at
        if (
            not force
            and next_scan_at is not None
            and (next_scan_at if next_scan_at.tzinfo else next_scan_at.replace(tzinfo=timezone.utc)) > now
        ):
            logger.info("⏭️ Feed scan %s is not due yet; stale task ignored", feed_scroll_job_id)
            release_dispatch_lease(redis_client, dispatch_key, dispatch_token)
            return {"status": "skipped", "reason": "not_due"}

        account_email = job.account_email
        # A job may request fewer, but never more than MAX_POSTS_KEPT — every
        # scan keeps up to the top twenty scored posts.
        keep_limit = max(
            1,
            min(job.posts_per_scan or DEFAULT_POSTS_PER_SCAN, MAX_POSTS_KEPT),
        )

    # Run the async browser automation
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(
            _run_feed_scroll_async(account_email, keep_limit, job)
        )
        loop.close()
    except Exception as e:
        logger.error(f"Feed scroll failed for job {feed_scroll_job_id}: {e}")
        results = {"posts": [], "error": str(e)}

    # Store results in DB
    with get_sync_db() as db:
        job = db.query(FeedScrollJob).filter(FeedScrollJob.id == feed_scroll_job_id).first()
        if not job:
            release_dispatch_lease(redis_client, dispatch_key, dispatch_token)
            return {"status": "skipped", "reason": "job_deleted_during_scan"}

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

        # Only keep posts that actually matched the criteria.  A score of 0
        # means the post matched nothing (or was empty) and is irrelevant, so
        # we drop anything with score <= 1 (score must be greater than 1).
        relevant_posts = [p for p in scored_posts if p.get("score", 0) > 1.0]
        relevant_posts.sort(key=lambda x: x["score"], reverse=True)

        # Dedupe within this scan first.  The same LinkedIn card can be found
        # through multiple DOM wrappers (or as /feed/update and /posts links),
        # so score sorting alone can otherwise store repeated cards.
        unique_relevant_posts = []
        seen_keys = set()
        for post_data in relevant_posts:
            key = _post_identity_key(
                post_data.get("post_urn"), post_data.get("post_url"),
                post_data.get("author_name"), post_data.get("post_text")
            )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            unique_relevant_posts.append(post_data)

        # Also skip posts already stored for this job in earlier batches, so the
        # results page does not fill with the same post on every scheduled scan.
        existing_rows = (
            db.query(
                FeedScrollResult.post_urn,
                FeedScrollResult.post_url,
                FeedScrollResult.author_name,
                FeedScrollResult.post_text,
            )
            .filter(FeedScrollResult.feed_scroll_job_id == job.id)
            .all()
        )
        existing_keys = {
            _post_identity_key(row.post_urn, row.post_url, row.author_name, row.post_text)
            for row in existing_rows
        }

        # Crossmatch against posts permanently marked as applied by the user
        # so applied posts are never duplicated or resurfaced on subsequent scans.
        applied_rows = (
            db.query(
                FeedScrollAppliedPost.post_urn,
                FeedScrollAppliedPost.post_url,
                FeedScrollAppliedPost.author_profile_url,
            )
            .filter(
                (FeedScrollAppliedPost.feed_scroll_job_id == job.id)
                | (FeedScrollAppliedPost.owner_email == job.owner_email)
            )
            .all()
        )
        applied_urns = {row.post_urn for row in applied_rows if row.post_urn}
        applied_urls = {row.post_url for row in applied_rows if row.post_url}

        top_posts = []
        skipped_existing = 0
        skipped_applied = 0
        skipped_missing_urls = 0
        for post_data in unique_relevant_posts:
            # Every stored result must include both outbound LinkedIn links.
            # Re-validate here even though extraction does it too: workers may
            # receive posts from a future collector or a retry with partial data.
            resolved_urls = _resolve_result_urls(
                post_data.get("post_url"),
                post_data.get("post_urn"),
                post_data.get("author_profile_url"),
            )
            if not resolved_urls:
                skipped_missing_urls += 1
                if should_log_debug():
                    logger.debug(
                        "Skipping post without both resolvable LinkedIn URLs: %s",
                        post_data.get("post_urn"),
                    )
                continue
            post_url, author_profile_url = resolved_urls
            post_data["post_url"] = post_url
            post_data["author_profile_url"] = author_profile_url

            # Skip if already marked as applied by the user
            if (post_data.get("post_urn") and post_data.get("post_urn") in applied_urns) or post_url in applied_urls:
                skipped_applied += 1
                if should_log_debug():
                    logger.debug(
                        "Skipping already applied post: %s (%s)",
                        post_data.get("post_urn"),
                        post_url,
                    )
                continue

            key = _post_identity_key(
                post_data.get("post_urn"), post_url,
                post_data.get("author_name"), post_data.get("post_text")
            )
            if key in existing_keys:
                skipped_existing += 1
                continue
            top_posts.append(post_data)
            if len(top_posts) >= keep_limit:
                break

        logger.info(
            f"🏆 Top {len(top_posts)} new unique posts after scoring "
            f"(from {len(raw_posts)} raw, {len(scored_posts)} scored, "
            f"{len(relevant_posts)} with score > 1, "
            f"{len(unique_relevant_posts)} unique, {skipped_missing_urls} without "
            f"both post/profile links, {skipped_existing} already stored, "
            f"{skipped_applied} already applied). "
            f"Highest score: {top_posts[0]['score'] if top_posts else 0}"
        )

        if len(top_posts) == 0 and unique_relevant_posts:
            logger.warning("⚠️ Relevant posts were found, but all were duplicates from earlier scans")
        elif len(top_posts) == 0 and raw_posts:
            logger.warning("⚠️ Had raw posts but none survived scoring")
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
                author_first_name=post_data.get("author_first_name"),
                author_last_name=post_data.get("author_last_name"),
                author_profile_url=post_data.get("author_profile_url"),
                connection_degree=post_data.get("connection_degree"),
                post_time=post_data.get("post_time"),
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
        job.remaining_seconds = None

        logger.info(
            f"✅ Feed scan complete for job {feed_scroll_job_id}: "
            f"{len(top_posts)} posts stored | top score {top_posts[0]['score'] if top_posts else 0} | batch={scan_batch_id}"
        )

        # ``next_scan_at`` is the durable schedule.  Do not add a long ETA
        # Celery message here: it survives a deleted/paused job in Redis and
        # creates the background tasks operators see after cleanup. Beat's
        # dispatcher will enqueue the next run when this timestamp is due.

    release_dispatch_lease(redis_client, dispatch_key, dispatch_token)
    return {"status": "completed", "job_id": feed_scroll_job_id}


@celery_app.task(name="tasks.dispatch_due_feed_scans")
def dispatch_due_feed_scans():
    """Queue feed scroll scans whose due time (next_scan_at) has arrived.

    Run periodically by Celery Beat so that if the worker or application
    was closed/restarted, overdue scans are dispatched reliably without
    relying solely on in-memory Celery timers.
    """
    import redis

    now = datetime.now(timezone.utc)
    with get_sync_db() as db:
        due_jobs = (
            db.query(FeedScrollJob)
            .filter(
                FeedScrollJob.status == FeedScrollJobStatus.ACTIVE,
                FeedScrollJob.next_scan_at != None,
                FeedScrollJob.next_scan_at <= now,
            )
            .all()
        )
        # Claim a Redis lease before publishing.  Leave ``next_scan_at``
        # untouched: the worker can then reject an old queued task if the job
        # has already completed, been paused, or been deleted.
        job_ids = [j.id for j in due_jobs]

    redis_client = redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=0.25, socket_timeout=0.25
    )
    dispatched = 0
    for job_id in job_ids:
        dispatch_key = f"linkeasy:scheduler:feed:{job_id}"
        redis_error = False
        try:
            lease_token = claim_dispatch_lease(redis_client, dispatch_key, timeout=7200)
        except Exception:
            # Redis is also the broker, so this normally fails the dispatch
            # immediately. Keep the fallback for lightweight maintenance/test
            # environments where the DB dispatcher is called in isolation.
            lease_token = None
            redis_error = True
        if not lease_token and not redis_error:
            continue
        if redis_error:
            logger.warning("Could not claim feed scheduler lease for %s", job_id)
        try:
            task_args = [job_id, lease_token] if lease_token else [job_id]
            celery_app.send_task("tasks.run_feed_scroll", args=task_args)
            dispatched += 1
        except Exception:
            release_dispatch_lease(redis_client, dispatch_key, lease_token)
            raise

    if dispatched:
        logger.info(f"📅 Dispatched {dispatched} due feed scroll scan(s)")
    return {"scans_dispatched": dispatched}


async def _run_feed_scroll_async(account_email: str, keep_limit: int, job) -> dict:
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

                    # Scroll feed and collect 20-30 posts per scan; the scorer
                    # keeps up to the top 20 after ranking them.
                    posts = await scroll_feed_and_collect(
                        page,
                        target_posts=random.randint(COLLECT_MIN_POSTS, COLLECT_MAX_POSTS),
                        max_scrolls=20,
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
    """Compatibility no-op for the retired ETA scheduling path.

    Feed jobs are scheduled from ``FeedScrollJob.next_scan_at`` by Beat.  The
    helper remains importable for older integrations but never places an ETA
    message in Redis.
    """
    logger.info(
        "Skipping retired ETA feed task for job %s; durable scheduler owns the next scan",
        job_id,
    )

"""
Feed Scroll API endpoints.
FILE: api/v1/feed_scroll.py

POST   /api/v1/feed-scroll/jobs          — create feed scroll job
GET    /api/v1/feed-scroll/jobs          — list user's feed scroll jobs
GET    /api/v1/feed-scroll/jobs/{id}     — get single job
PATCH  /api/v1/feed-scroll/jobs/{id}     — update job (name, criteria, interval,
                                        status; criteria edits re-score stored
                                        results so the next scan picks up posts
                                        matching the new criteria)
DELETE /api/v1/feed-scroll/jobs/{id}     — delete job
GET    /api/v1/feed-scroll/jobs/{id}/results — get scored posts for a job
POST   /api/v1/feed-scroll/jobs/{id}/scan    — trigger immediate manual scan
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_db
from models.feed_scroll_job import (
    MAX_POSTS_PER_SCAN,
    FeedScrollJob,
    FeedScrollJobStatus,
    FeedScrollMode,
)
from models.feed_scroll_result import FeedScrollResult
from models.feed_scroll_applied_post import FeedScrollAppliedPost
from models.linkedin_account import LinkedInAccount
from automation.actions.feed_scroll import _post_identity_key, _resolve_result_urls
from schemas.feed_scroll import (
    FeedScrollAppliedPostCreate,
    FeedScrollAppliedPostResponse,
    FeedScrollBulkDeleteRequest,
    FeedScrollJobCreate,
    FeedScrollJobResponse,
    FeedScrollJobUpdate,
    FeedScrollResultResponse,
)

router = APIRouter(prefix="/api/v1/feed-scroll", tags=["feed-scroll"])

# The endpoint mirrors the worker's per-scan cap.  Fetch extra candidates from
# storage because legacy rows without both URLs are filtered at response time.
RESULTS_PAGE_LIMIT = MAX_POSTS_PER_SCAN
RESULTS_PAGE_CANDIDATE_LIMIT = RESULTS_PAGE_LIMIT * 25


def _prepare_unique_results(rows, max_items: int | None = None) -> list[FeedScrollResult]:
    """Normalize links and remove repeated posts before returning them.

    Older scans may have stored relative LinkedIn hrefs (``/posts/...``), no
    ``post_url``, or no author profile URL.  Normalize rows at response time so
    valid legacy links stay useful, but only surface a result when *both* its
    post URL and author profile URL resolve to LinkedIn.  Dedupe by activity id
    / URL / text hash so repeated scheduled scans do not show the same post
    multiple times on the results page.
    """
    unique: list[FeedScrollResult] = []
    seen_keys: set[str] = set()
    for row in rows:
        resolved_urls = _resolve_result_urls(
            row.post_url, row.post_urn, row.author_profile_url
        )
        if not resolved_urls:
            # Both links are a product invariant for a surfaced result.
            continue
        row.post_url, row.author_profile_url = resolved_urls
        key = _post_identity_key(row.post_urn, row.post_url, row.author_name, row.post_text)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique.append(row)
        if max_items is not None and len(unique) >= max_items:
            break
    return unique


@router.post("/jobs", response_model=FeedScrollJobResponse, status_code=status.HTTP_201_CREATED)
async def create_feed_scroll_job(
    payload: FeedScrollJobCreate,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollJobResponse:
    """Create a new feed scroll job."""
    # Verify LinkedIn account belongs to the owner
    account_result = await db.execute(
        select(LinkedInAccount).where(
            LinkedInAccount.linkedin_email == payload.account_email,
            LinkedInAccount.owner_email == payload.owner_email,
        )
    )
    account = account_result.scalars().first()
    if not account:
        raise HTTPException(status_code=400, detail="LinkedIn account not found or does not belong to you")

    job = FeedScrollJob(
        id=str(uuid.uuid4()),
        account_email=payload.account_email,
        owner_email=payload.owner_email,
        name=payload.name,
        mode=payload.mode,
        status=FeedScrollJobStatus.DRAFT,
        experience_min_years=payload.experience_min_years,
        experience_max_years=payload.experience_max_years,
        job_titles=payload.job_titles,
        skill_set=payload.skill_set,
        keywords=payload.keywords,
        feed_interval_hours=payload.feed_interval_hours,
        posts_per_scan=payload.posts_per_scan,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return FeedScrollJobResponse.model_validate(job)


@router.get("/jobs", response_model=list[FeedScrollJobResponse])
async def list_feed_scroll_jobs(
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> list[FeedScrollJobResponse]:
    """List all feed scroll jobs for a user."""
    result = await db.execute(
        select(FeedScrollJob)
        .where(FeedScrollJob.owner_email == owner_email)
        .order_by(FeedScrollJob.created_at.desc())
    )
    jobs = result.scalars().all()
    return [FeedScrollJobResponse.model_validate(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=FeedScrollJobResponse)
async def get_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollJobResponse:
    """Get a single feed scroll job."""
    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")
    return FeedScrollJobResponse.model_validate(job)


def _ensure_job_criteria(job: FeedScrollJob) -> None:
    """Reject an edit that would leave the job with no usable search criteria."""
    if job.mode == FeedScrollMode.JOB_SEARCH:
        if not any((job.job_titles, job.skill_set, job.keywords)):
            raise HTTPException(
                status_code=400,
                detail="Job search requires at least one job title, skill, or keyword",
            )
    elif not job.keywords:
        raise HTTPException(
            status_code=400,
            detail="Post search requires at least one keyword",
        )


async def _re_score_results(db: AsyncSession, job: FeedScrollJob) -> tuple[int, int]:
    """Re-score every stored result against the job's current criteria.

    Rows whose score drops to the relevance floor (<= 1) no longer match the
    edited criteria and are deleted, so the results page and the next scan
    only reflect the new keywords / experience / job titles.  Posts that
    survive keep their updated score and matched terms.

    Returns ``(kept, removed)``.
    """
    from automation.scoring.feed_scorer import score_post

    config = {
        "mode": job.mode.value,
        "job_titles": job.job_titles or [],
        "skill_set": job.skill_set or [],
        "experience_min_years": job.experience_min_years,
        "experience_max_years": job.experience_max_years,
        "keywords": job.keywords or [],
    }

    result = await db.execute(
        select(FeedScrollResult).where(FeedScrollResult.feed_scroll_job_id == job.id)
    )
    rows = result.scalars().all()

    kept = removed = 0
    for row in rows:
        score, matched_terms = score_post(row.post_text or "", config)
        if score <= 1.0:
            await db.delete(row)
            removed += 1
        else:
            row.score = score
            row.matched_terms = matched_terms
            kept += 1
    return kept, removed


@router.patch("/jobs/{job_id}", response_model=FeedScrollJobResponse)
async def update_feed_scroll_job(
    job_id: str,
    payload: FeedScrollJobUpdate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollJobResponse:
    """Update a feed scroll job (name, criteria, interval, status).

    Every field present in the payload is applied verbatim — including nulls
    and empty lists, which clear experience bounds / tag fields; omitted
    fields are left untouched.

    When any search-criteria field changes (experience, job titles, skills,
    keywords), all stored results are re-scored against the new criteria:
    posts that no longer match are removed and survivors get fresh scores, so
    the next scan picks up posts matching the new criteria instead of mixing
    old and new match rules.
    """
    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    updates = payload.model_dump(exclude_unset=True)
    criteria_fields = {
        "experience_min_years",
        "experience_max_years",
        "job_titles",
        "skill_set",
        "keywords",
    }
    criteria_changed = bool(criteria_fields.intersection(updates))

    for field, value in updates.items():
        setattr(job, field, value)

    # The merged job must keep at least one usable criterion for its mode.
    _ensure_job_criteria(job)

    rescored = removed = None
    if criteria_changed:
        rescored, removed = await _re_score_results(db, job)

    await db.commit()
    await db.refresh(job)

    response = FeedScrollJobResponse.model_validate(job)
    if criteria_changed:
        response.rescored_results = rescored
        response.removed_results = removed
    return response


@router.delete("/jobs/{job_id}", status_code=200)
async def delete_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a feed scroll job and all its results."""
    from sqlalchemy import delete as sa_delete

    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    # Release a pending Beat lease. The worker still re-checks the row, so an
    # already-running task exits without opening a browser after deletion.
    try:
        import redis
        from core.config import settings

        redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        ).delete(f"linkeasy:scheduler:feed:{job_id}")
    except Exception:
        pass

    # Delete results first
    await db.execute(
        sa_delete(FeedScrollResult).where(FeedScrollResult.feed_scroll_job_id == job_id)
    )
    # Delete the job
    await db.execute(
        sa_delete(FeedScrollJob).where(FeedScrollJob.id == job_id)
    )
    await db.commit()

    return {"message": f"Feed scroll job '{job.name}' deleted successfully"}


@router.get("/jobs/{job_id}/results", response_model=list[FeedScrollResultResponse])
async def get_feed_scroll_results(
    job_id: str,
    owner_email: str,
    scan_batch_id: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[FeedScrollResultResponse]:
    """
    Get scored posts for a feed scroll job.

    If scan_batch_id is provided, returns that scan's top 20 scored posts.
    Otherwise returns the 20 highest-scoring unique posts for the job.
    """
    # Verify job ownership
    job_result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = job_result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    # Only return posts that actually matched the criteria.  Posts with a
    # score of 0 (or below the relevance floor) are never surfaced — score
    # must be greater than 1.  Dismissed posts (read and marked "not useful")
    # are filtered out so the user sees a clean list of posts still worth
    # acting on.
    _base = select(FeedScrollResult).where(
        FeedScrollResult.feed_scroll_job_id == job_id,
        FeedScrollResult.score > 1.0,
        FeedScrollResult.dismissed_at.is_(None),
    )

    if scan_batch_id:
        # Get a specific batch, ranked by score, with duplicate cards removed.
        result = await db.execute(
            _base.where(FeedScrollResult.scan_batch_id == scan_batch_id)
            .order_by(FeedScrollResult.score.desc(), FeedScrollResult.scanned_at.desc())
        )
        posts = _prepare_unique_results(
            result.scalars().all(), max_items=RESULTS_PAGE_LIMIT
        )
    else:
        # Rank by score before applying the result cap.  Query extra rows first
        # because deduplication and the two-URL invariant can discard legacy
        # data; the page should still receive up to twenty valid results.
        result = await db.execute(
            _base.order_by(FeedScrollResult.score.desc(), FeedScrollResult.scanned_at.desc())
            .limit(RESULTS_PAGE_CANDIDATE_LIMIT)
        )
        posts = _prepare_unique_results(
            result.scalars().all(), max_items=RESULTS_PAGE_LIMIT
        )

    # Crossmatch against applied posts so the results view immediately indicates
    # which posts have already been marked as applied.
    applied_result = await db.execute(
        select(FeedScrollAppliedPost).where(
            FeedScrollAppliedPost.feed_scroll_job_id == job_id,
            FeedScrollAppliedPost.owner_email == owner_email,
        )
    )
    applied_rows = applied_result.scalars().all()
    applied_urls = {ap.post_url: ap.applied_at for ap in applied_rows if ap.post_url}
    applied_urns = {ap.post_urn: ap.applied_at for ap in applied_rows if ap.post_urn}

    responses = []
    for p in posts:
        resp = FeedScrollResultResponse.model_validate(p)
        if p.post_url in applied_urls or (p.post_urn and p.post_urn in applied_urns):
            resp.is_applied = True
            resp.applied_at = applied_urls.get(p.post_url) or applied_urns.get(p.post_urn)
        responses.append(resp)

    return responses


@router.post("/jobs/{job_id}/results/{result_id}/apply", response_model=FeedScrollAppliedPostResponse)
async def mark_post_applied(
    job_id: str,
    result_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollAppliedPostResponse:
    """Mark a scanned feed post as applied.

    Stores the post permanently in `feed_scroll_applied_posts` and ensures
    subsequent scans automatically crossmatch and skip it to prevent duplication.
    """
    owned = await _load_owned_result(job_id, result_id, owner_email, db)
    if not owned:
        raise HTTPException(status_code=404, detail="Feed scroll result not found")
    job, result = owned

    # Check if already marked as applied
    existing_q = await db.execute(
        select(FeedScrollAppliedPost).where(
            FeedScrollAppliedPost.feed_scroll_job_id == job_id,
            FeedScrollAppliedPost.owner_email == owner_email,
            (
                (FeedScrollAppliedPost.post_url == result.post_url)
                | (
                    (FeedScrollAppliedPost.post_urn != None)
                    & (FeedScrollAppliedPost.post_urn == result.post_urn)
                )
            ),
        )
    )
    existing = existing_q.scalars().first()
    if existing:
        return FeedScrollAppliedPostResponse.model_validate(existing)

    applied_post = FeedScrollAppliedPost(
        id=str(uuid.uuid4()),
        feed_scroll_job_id=job.id,
        owner_email=owner_email,
        post_urn=result.post_urn,
        post_url=result.post_url,
        author_name=result.author_name,
        author_first_name=result.author_first_name,
        author_last_name=result.author_last_name,
        author_profile_url=result.author_profile_url,
        connection_degree=result.connection_degree,
        post_time=result.post_time,
        post_text=result.post_text,
        score=result.score,
        matched_terms=result.matched_terms,
        scan_batch_id=result.scan_batch_id,
        applied_at=datetime.now(timezone.utc),
    )
    db.add(applied_post)
    await db.commit()
    await db.refresh(applied_post)

    return FeedScrollAppliedPostResponse.model_validate(applied_post)


@router.post("/jobs/{job_id}/applied-posts", response_model=FeedScrollAppliedPostResponse)
async def create_applied_post(
    job_id: str,
    payload: FeedScrollAppliedPostCreate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollAppliedPostResponse:
    """Manually add or record a post as applied for a feed scroll job."""
    job_result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = job_result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    applied_post = FeedScrollAppliedPost(
        id=str(uuid.uuid4()),
        feed_scroll_job_id=job.id,
        owner_email=owner_email,
        post_urn=payload.post_urn,
        post_url=payload.post_url,
        author_name=payload.author_name,
        author_first_name=payload.author_first_name,
        author_last_name=payload.author_last_name,
        author_profile_url=payload.author_profile_url,
        connection_degree=payload.connection_degree,
        post_time=payload.post_time,
        post_text=payload.post_text,
        score=payload.score or 0.0,
        matched_terms=payload.matched_terms,
        scan_batch_id=payload.scan_batch_id,
        applied_at=datetime.now(timezone.utc),
    )
    db.add(applied_post)
    await db.commit()
    await db.refresh(applied_post)

    return FeedScrollAppliedPostResponse.model_validate(applied_post)


@router.get("/jobs/{job_id}/applied-posts", response_model=list[FeedScrollAppliedPostResponse])
async def list_applied_posts(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> list[FeedScrollAppliedPostResponse]:
    """Get all posts marked as applied for a feed scroll job."""
    job_result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = job_result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    result = await db.execute(
        select(FeedScrollAppliedPost)
        .where(
            FeedScrollAppliedPost.feed_scroll_job_id == job_id,
            FeedScrollAppliedPost.owner_email == owner_email,
        )
        .order_by(FeedScrollAppliedPost.applied_at.desc())
    )
    posts = result.scalars().all()
    return [FeedScrollAppliedPostResponse.model_validate(p) for p in posts]


@router.delete("/jobs/{job_id}/applied-posts/{applied_id}", status_code=200)
async def delete_applied_post(
    job_id: str,
    applied_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete a single applied post entry."""
    result = await db.execute(
        select(FeedScrollAppliedPost).where(
            FeedScrollAppliedPost.id == applied_id,
            FeedScrollAppliedPost.feed_scroll_job_id == job_id,
            FeedScrollAppliedPost.owner_email == owner_email,
        )
    )
    post = result.scalars().first()
    if not post:
        raise HTTPException(status_code=404, detail="Applied post not found")

    await db.delete(post)
    await db.commit()
    return {"message": "Applied post deleted successfully", "id": applied_id}


@router.post("/jobs/{job_id}/applied-posts/bulk-delete", status_code=200)
async def bulk_delete_applied_posts(
    job_id: str,
    payload: FeedScrollBulkDeleteRequest,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Delete multiple applied post entries by ID."""
    from sqlalchemy import delete as sa_delete

    job_result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = job_result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    if not payload.post_ids:
        return {"message": "No posts provided to delete", "deleted_count": 0, "deleted_ids": []}

    stmt = sa_delete(FeedScrollAppliedPost).where(
        FeedScrollAppliedPost.id.in_(payload.post_ids),
        FeedScrollAppliedPost.feed_scroll_job_id == job_id,
        FeedScrollAppliedPost.owner_email == owner_email,
    )
    res = await db.execute(stmt)
    await db.commit()

    return {
        "message": f"{res.rowcount} applied post(s) deleted successfully",
        "deleted_count": res.rowcount,
        "deleted_ids": payload.post_ids,
    }


async def _load_owned_result(
    job_id: str, result_id: str, owner_email: str, db: AsyncSession
) -> tuple[FeedScrollJob, FeedScrollResult] | None:
    """Return the job + result if both exist and belong to the owner.

    Returns ``None`` when either the job or the result is missing or not owned
    by this user, so callers can return a uniform 404.
    """
    job_result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = job_result.scalars().first()
    if not job:
        return None

    result_query = await db.execute(
        select(FeedScrollResult).where(
            FeedScrollResult.id == result_id,
            FeedScrollResult.feed_scroll_job_id == job_id,
        )
    )
    result = result_query.scalars().first()
    if not result:
        return None

    return job, result


@router.delete("/jobs/{job_id}/results/{result_id}", status_code=200)
async def dismiss_feed_scroll_result(
    job_id: str,
    result_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Remove a single scanned post from the results view.

    This is a soft dismiss: the row is flagged with ``dismissed_at`` rather than
    deleted, so it disappears from the list but still counts as "already stored"
    for the scanner's de-dup — a dismissed post therefore never reappears on the
    next scheduled scan.  Idempotent: dismissing something already dismissed is a
    no-op 200, and a missing/foreign result is a 404.  Dismissing a post whose
    author was saved to a Feed Leads pool does not touch that pool entry.
    """
    owned = await _load_owned_result(job_id, result_id, owner_email, db)
    if not owned:
        raise HTTPException(status_code=404, detail="Feed scroll result not found")
    _job, result = owned

    result.dismissed_at = datetime.now(timezone.utc)
    await db.commit()

    return {"message": "Post removed from results", "id": result_id}


@router.post("/jobs/{job_id}/results/{result_id}/restore", status_code=200)
async def restore_feed_scroll_result(
    job_id: str,
    result_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Undo a dismiss so the post shows up in the results view again.

    Used by the "Undo" affordance after a quick removal.  Returns 404 when the
    result is missing/not owned, and 200 (with ``restored: False``) when the
    post was not actually dismissed.
    """
    owned = await _load_owned_result(job_id, result_id, owner_email, db)
    if not owned:
        raise HTTPException(status_code=404, detail="Feed scroll result not found")
    _job, result = owned

    restored = result.dismissed_at is not None
    result.dismissed_at = None
    await db.commit()

    return {"message": "Post restored to results", "id": result_id, "restored": restored}


@router.post("/jobs/{job_id}/scan", status_code=200)
async def trigger_manual_scan(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Trigger an immediate manual scan for a feed scroll job."""
    from worker.celery_app import celery_app

    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    # Manual scans are explicit and bypass the durable next_scan_at check. They
    # are published without an ETA so deleting/pausing a job never leaves a
    # delayed Redis message behind.
    celery_app.send_task("tasks.run_feed_scroll", args=[job.id, None, True])

    return {"message": "Manual scan queued. Results will be available shortly."}


@router.post("/jobs/{job_id}/activate", status_code=200)
async def activate_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Activate or resume a feed scroll job.

    If the job was paused with remaining time, resumes the remaining countdown
    from now so the remaining time starts dropping again from the time difference.
    Otherwise schedules the first scan.
    """
    from worker.celery_app import celery_app

    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    if job.status == FeedScrollJobStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Job is already active")

    now = datetime.now(timezone.utc)
    job.status = FeedScrollJobStatus.ACTIVE

    immediate_scan = False
    if job.remaining_seconds is not None and job.remaining_seconds > 0:
        delay_seconds = job.remaining_seconds
        job.next_scan_at = now + timedelta(seconds=delay_seconds)
        job.remaining_seconds = None
        message = f"Job '{job.name}' resumed. Next scan in {delay_seconds} seconds."
    else:
        # The first scan is due now. Publish an immediate, force-marked task
        # only after the database commit; later scans are Beat-dispatched from
        # next_scan_at with no Celery countdown messages.
        delay_seconds = 0
        job.remaining_seconds = None
        job.next_scan_at = now
        immediate_scan = True
        message = f"Job '{job.name}' activated. First scan starting..."

    await db.commit()
    if immediate_scan:
        celery_app.send_task("tasks.run_feed_scroll", args=[job.id, None, True])
    return {"message": message}


@router.post("/jobs/{job_id}/pause", status_code=200)
async def pause_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Pause an active feed scroll job and preserve remaining scan time."""
    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    if job.status != FeedScrollJobStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Job is not active")

    now = datetime.now(timezone.utc)
    if job.next_scan_at:
        next_at = job.next_scan_at if job.next_scan_at.tzinfo else job.next_scan_at.replace(tzinfo=timezone.utc)
        if next_at > now:
            job.remaining_seconds = max(0, int((next_at - now).total_seconds()))
        else:
            job.remaining_seconds = 0
    else:
        job.remaining_seconds = job.feed_interval_hours * 3600

    job.status = FeedScrollJobStatus.PAUSED
    await db.commit()
    try:
        import redis
        from core.config import settings

        redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        ).delete(f"linkeasy:scheduler:feed:{job_id}")
    except Exception:
        pass

    return {"message": f"Job '{job.name}' paused", "remaining_seconds": job.remaining_seconds}

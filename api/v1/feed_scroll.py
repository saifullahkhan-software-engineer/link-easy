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
from datetime import datetime, timezone

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
from models.linkedin_account import LinkedInAccount
from automation.actions.feed_scroll import _post_identity_key, _resolve_result_urls
from schemas.feed_scroll import (
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
    # must be greater than 1.
    _base = select(FeedScrollResult).where(
        FeedScrollResult.feed_scroll_job_id == job_id,
        FeedScrollResult.score > 1.0,
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

    return [FeedScrollResultResponse.model_validate(p) for p in posts]


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

    # Enqueue the scan task immediately
    celery_app.send_task("tasks.run_feed_scroll", args=[job.id], countdown=5)

    return {"message": "Manual scan queued. Results will be available shortly."}


@router.post("/jobs/{job_id}/activate", status_code=200)
async def activate_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Activate a feed scroll job and schedule the first scan."""
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

    job.status = FeedScrollJobStatus.ACTIVE
    job.next_scan_at = datetime.now(timezone.utc)

    # Schedule first scan immediately
    celery_app.send_task("tasks.run_feed_scroll", args=[job.id], countdown=10)

    await db.commit()

    return {"message": f"Job '{job.name}' activated. First scan starting..."}


@router.post("/jobs/{job_id}/pause", status_code=200)
async def pause_feed_scroll_job(
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
):
    """Pause an active feed scroll job."""
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

    job.status = FeedScrollJobStatus.PAUSED
    await db.commit()

    return {"message": f"Job '{job.name}' paused"}

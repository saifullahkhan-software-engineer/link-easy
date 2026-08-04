"""
Feed Scroll API endpoints.
FILE: api/v1/feed_scroll.py

POST   /api/v1/feed-scroll/jobs          — create feed scroll job
GET    /api/v1/feed-scroll/jobs          — list user's feed scroll jobs
GET    /api/v1/feed-scroll/jobs/{id}     — get single job
PATCH  /api/v1/feed-scroll/jobs/{id}     — update job (pause/resume)
DELETE /api/v1/feed-scroll/jobs/{id}     — delete job
GET    /api/v1/feed-scroll/jobs/{id}/results — get scored posts for a job
POST   /api/v1/feed-scroll/jobs/{id}/scan    — trigger immediate manual scan
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_current_user, get_db
from models.feed_scroll_job import FeedScrollJob, FeedScrollJobStatus, FeedScrollMode
from models.feed_scroll_result import FeedScrollResult
from models.linkedin_account import LinkedInAccount
from models.user import User
from automation.actions.feed_scroll import _normalise_post_url, _post_identity_key
from schemas.feed_scroll import (
    FeedScrollJobCreate,
    FeedScrollJobResponse,
    FeedScrollJobUpdate,
    FeedScrollResultResponse,
)

router = APIRouter(prefix="/api/v1/feed-scroll", tags=["feed-scroll"])


def _prepare_unique_results(rows, max_items: int | None = None) -> list[FeedScrollResult]:
    """Normalize post links and remove repeated posts before returning them.

    Older scans may have stored relative LinkedIn hrefs (``/posts/...``) or no
    ``post_url`` at all.  Normalize those at response time so existing rows are
    clickable without needing a data backfill.  Dedupe by activity id / URL /
    text hash so repeated scheduled scans do not show the same post multiple
    times on the results page.
    """
    unique: list[FeedScrollResult] = []
    seen_keys: set[str] = set()
    for row in rows:
        row.post_url = _normalise_post_url(row.post_url, row.post_urn)
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


@router.patch("/jobs/{job_id}", response_model=FeedScrollJobResponse)
async def update_feed_scroll_job(
    job_id: str,
    payload: FeedScrollJobUpdate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> FeedScrollJobResponse:
    """Update a feed scroll job (name, criteria, interval, status)."""
    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Feed scroll job not found")

    # Update fields
    if payload.name is not None:
        job.name = payload.name
    if payload.status is not None:
        job.status = payload.status
    if payload.experience_min_years is not None:
        job.experience_min_years = payload.experience_min_years
    if payload.experience_max_years is not None:
        job.experience_max_years = payload.experience_max_years
    if payload.job_titles is not None:
        job.job_titles = payload.job_titles
    if payload.skill_set is not None:
        job.skill_set = payload.skill_set
    if payload.keywords is not None:
        job.keywords = payload.keywords
    if payload.feed_interval_hours is not None:
        job.feed_interval_hours = payload.feed_interval_hours
    if payload.posts_per_scan is not None:
        job.posts_per_scan = payload.posts_per_scan

    await db.commit()
    await db.refresh(job)
    return FeedScrollJobResponse.model_validate(job)


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

    If scan_batch_id is provided, returns results from that specific scan.
    Otherwise returns the latest scan's top 10 posts.
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
        # Get specific batch, with duplicate cards removed.
        result = await db.execute(
            _base.where(FeedScrollResult.scan_batch_id == scan_batch_id)
            .order_by(FeedScrollResult.score.desc(), FeedScrollResult.scanned_at.desc())
        )
        posts = _prepare_unique_results(result.scalars().all())
    else:
        # Get recent rows then dedupe in Python.  Query more than 10 because a
        # scheduled scan can collect the same post again; after removing repeats
        # we still want up to 10 unique, clickable results.
        result = await db.execute(
            _base.order_by(FeedScrollResult.scanned_at.desc(), FeedScrollResult.score.desc()).limit(200)
        )
        posts = _prepare_unique_results(result.scalars().all(), max_items=10)

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

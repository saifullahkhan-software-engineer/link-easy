"""
Feed Leads pool endpoints.
FILE: api/v1/feed_leads.py

The pool stages profiles saved from Feed Scroll scan results.  Saving never
touches the campaigns/leads tables — a pool entry only becomes a real lead when
the user imports it from a campaign's "Feed Leads" tab
(``POST /api/v1/campaigns/{id}/leads/import-feed-leads``), which reuses the
same validation/insert path as CSV and manual import.

POST   /api/v1/feed-leads              — save a scanned profile into a pool
GET    /api/v1/feed-leads              — list pool entries (default: saved only)
GET    /api/v1/feed-leads/pools        — pools (feed scroll jobs) + counts
DELETE /api/v1/feed-leads/{id}         — discard a saved profile from the pool
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_db
from models.feed_lead import FEED_LEAD_SOURCE, FeedLead, FeedLeadStatus
from models.feed_scroll_job import FeedScrollJob
from schemas.feed_lead import FeedLeadCreate, FeedLeadPoolResponse, FeedLeadResponse
from schemas.lead import validate_lead_fields

router = APIRouter(prefix="/api/v1/feed-leads", tags=["feed-leads"])


async def _get_job_or_404(job_id: str, owner_email: str, db: AsyncSession) -> FeedScrollJob:
    """Fetch a feed scroll job (= pool) and verify it belongs to the owner."""
    result = await db.execute(
        select(FeedScrollJob).where(
            FeedScrollJob.id == job_id,
            FeedScrollJob.owner_email == owner_email,
        )
    )
    job = result.scalars().first()
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feed scroll job not found or does not belong to you",
        )
    return job


@router.post(
    "",
    response_model=FeedLeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Save a scanned profile into a feed leads pool",
)
async def save_feed_lead(
    payload: FeedLeadCreate,
    db: AsyncSession = Depends(get_db),
) -> FeedLeadResponse:
    """
    Stores the author of a scored post in the pool of a feed scroll job.

    Validation is identical to CSV/manual lead import (first_name, last_name
    and a valid ``/in/`` LinkedIn URL are required, headline optional).  If the
    same profile is already waiting in this pool the request fails with 409 so
    the caller can show the "Saved" state instead of creating a duplicate.
    """
    job = await _get_job_or_404(payload.feed_scroll_job_id, payload.owner_email, db)

    try:
        cleaned = validate_lead_fields(payload.first_name, payload.last_name, payload.linkedin_url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    existing_result = await db.execute(
        select(FeedLead).where(
            FeedLead.feed_scroll_job_id == job.id,
            FeedLead.linkedin_url == cleaned["linkedin_url"],
            FeedLead.status == FeedLeadStatus.SAVED,
        )
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Already in \"{job.name}\" feed leads",
                "code": "already_in_pool",
                "feed_lead_id": existing.id,
                "feed_scroll_job_id": job.id,
                "pool_name": job.name,
            },
        )

    feed_lead = FeedLead(
        id=str(uuid.uuid4()),
        owner_email=payload.owner_email,
        feed_scroll_job_id=job.id,
        feed_scroll_result_id=payload.feed_scroll_result_id,
        linkedin_url=cleaned["linkedin_url"],
        first_name=cleaned["first_name"],
        last_name=cleaned["last_name"],
        headline=(payload.headline or "").strip() or None,
        label=(payload.label or "").strip() or None,
        source=payload.source or FEED_LEAD_SOURCE,
        source_post_url=payload.source_post_url,
        matched_score=payload.matched_score,
        matched_criteria=payload.matched_criteria,
        scan_id=payload.scan_id,
        status=FeedLeadStatus.SAVED,
    )
    db.add(feed_lead)
    await db.commit()
    await db.refresh(feed_lead)
    return FeedLeadResponse.model_validate(feed_lead)


@router.get(
    "/pools",
    response_model=list[FeedLeadPoolResponse],
    summary="List feed lead pools (one per feed scroll job) with counts",
)
async def list_feed_lead_pools(
    owner_email: str = Query(..., description="Owner email for validation"),
    only_with_saved: bool = Query(False, description="Hide pools that have nothing waiting"),
    db: AsyncSession = Depends(get_db),
) -> list[FeedLeadPoolResponse]:
    """
    Returns every feed scroll job of the owner together with how many profiles
    are waiting in (or were already consumed from) its pool.  The campaign
    "Feed Leads" tab uses this to let the user pick which scan to pull from.
    """
    jobs_result = await db.execute(
        select(FeedScrollJob)
        .where(FeedScrollJob.owner_email == owner_email)
        .order_by(FeedScrollJob.created_at.desc())
    )
    jobs = jobs_result.scalars().all()

    counts_result = await db.execute(
        select(
            FeedLead.feed_scroll_job_id,
            FeedLead.status,
            func.count(FeedLead.id),
            func.max(FeedLead.created_at),
        )
        .where(FeedLead.owner_email == owner_email)
        .group_by(FeedLead.feed_scroll_job_id, FeedLead.status)
    )

    saved: dict[str, int] = {}
    imported: dict[str, int] = {}
    last_saved: dict[str, object] = {}
    for job_id, row_status, count, latest in counts_result.all():
        status_value = getattr(row_status, "value", row_status)
        if status_value == FeedLeadStatus.SAVED.value:
            saved[job_id] = count
            last_saved[job_id] = latest
        else:
            imported[job_id] = imported.get(job_id, 0) + count

    pools = [
        FeedLeadPoolResponse(
            feed_scroll_job_id=job.id,
            name=job.name,
            mode=getattr(job.mode, "value", job.mode),
            status=getattr(job.status, "value", job.status),
            saved_count=saved.get(job.id, 0),
            imported_count=imported.get(job.id, 0),
            last_saved_at=last_saved.get(job.id),
        )
        for job in jobs
    ]
    if only_with_saved:
        pools = [pool for pool in pools if pool.saved_count > 0]
    return pools


@router.get(
    "",
    response_model=list[FeedLeadResponse],
    summary="List saved feed leads",
)
async def list_feed_leads(
    owner_email: str = Query(..., description="Owner email for validation"),
    feed_scroll_job_id: str | None = Query(None, description="Restrict to one pool"),
    status_filter: FeedLeadStatus | None = Query(
        FeedLeadStatus.SAVED,
        alias="status",
        description="saved (default) | imported. Pass an empty value for both.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[FeedLeadResponse]:
    """
    Returns pool entries for the owner, newest first.

    Defaults to ``status=saved`` so the pool reads as an inbox that empties as
    the user imports from it; the Feed Scroll results page also asks for
    ``imported`` entries to keep post cards showing the right button state.
    """
    query = select(FeedLead).where(FeedLead.owner_email == owner_email)
    if feed_scroll_job_id:
        query = query.where(FeedLead.feed_scroll_job_id == feed_scroll_job_id)
    if status_filter is not None:
        query = query.where(FeedLead.status == status_filter)

    result = await db.execute(query.order_by(FeedLead.created_at.desc()))
    return [FeedLeadResponse.model_validate(row) for row in result.scalars().all()]


@router.delete(
    "/{feed_lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Discard a saved profile from the pool",
)
async def delete_feed_lead(
    feed_lead_id: str,
    owner_email: str = Query(..., description="Owner email for validation"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Removes a profile from the pool without ever touching campaign leads."""
    result = await db.execute(
        select(FeedLead).where(
            FeedLead.id == feed_lead_id,
            FeedLead.owner_email == owner_email,
        )
    )
    feed_lead = result.scalars().first()
    if feed_lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Feed lead not found")

    await db.delete(feed_lead)
    await db.commit()

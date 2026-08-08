"""
WhatsApp Job Scanner — API endpoints.
FILE: api/v1/whatsapp_scanner.py

POST   /api/whatsapp/connect          → start session task
GET    /api/whatsapp/status           → connection status
GET    /api/whatsapp/groups           → list all groups
POST   /api/whatsapp/groups/select    → save monitored + forward groups
GET    /api/whatsapp/filters          → get current filters
POST   /api/whatsapp/filters          → save filters
GET    /api/whatsapp/messages         → paginated list with scores/status
POST   /api/whatsapp/scan/trigger     → manually trigger scan task
GET    /api/whatsapp/stats            → matched/rejected/forwarded counts
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func as sa_func

from api.dependencies import get_db, get_current_user
from models.user import User
from schemas.whatsapp import (
    WhatsAppConnectResponse,
    WhatsAppStatusResponse,
    WhatsAppGroupListResponse,
    WhatsAppGroupItem,
    WhatsAppGroupSelectRequest,
    WhatsAppGroupSelectResponse,
    WhatsAppScanFilterRequest,
    WhatsAppScanFilterResponse,
    WhatsAppMessageResponse,
    WhatsAppMessageListResponse,
    WhatsAppStatsResponse,
)
from models.whatsapp import (
    WhatsAppSession,
    WhatsAppMonitoredGroup,
    WhatsAppForwardGroup,
    WhatsAppRawMessage,
    WhatsAppScanFilter,
)
from core.logging_config import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp-scanner"])


# ── POST /connect ────────────────────────────────────────────────────────────


@router.post("/connect", response_model=WhatsAppConnectResponse)
async def connect_whatsapp(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppConnectResponse:
    """Start a Celery task that launches a non-headless browser for WhatsApp QR login."""
    from worker.celery_app import celery_app

    # Check if there's already a task in progress
    result = await db.execute(
        select(WhatsAppSession).order_by(WhatsAppSession.id.desc()).limit(1)
    )
    existing = result.scalars().first()

    if existing and existing.status == "waiting_qr":
        return WhatsAppConnectResponse(
            message="A WhatsApp connection is already in progress — scan the QR code.",
            status="waiting_qr",
        )

    # Deactivate any existing sessions
    if existing:
        existing.is_active = False

    # Create a new pending session record
    new_session = WhatsAppSession(status="waiting_qr", is_active=True)
    db.add(new_session)
    await db.commit()

    # Start the Celery connect task
    celery_app.send_task("tasks.connect_whatsapp", countdown=2)

    logger.info("📱 WhatsApp connect task queued")
    return WhatsAppConnectResponse(
        message="WhatsApp connection started. Open the browser to scan QR code.",
        status="waiting_qr",
    )


# ── GET /status ──────────────────────────────────────────────────────────────


@router.get("/status", response_model=WhatsAppStatusResponse)
async def get_whatsapp_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppStatusResponse:
    """Return the current WhatsApp connection status."""
    result = await db.execute(
        select(WhatsAppSession)
        .order_by(WhatsAppSession.id.desc())
        .limit(1)
    )
    session = result.scalars().first()

    if not session:
        return WhatsAppStatusResponse(status="disconnected", is_active=False)

    return WhatsAppStatusResponse(
        status=session.status,
        is_active=session.is_active,
    )


# ── GET /groups ──────────────────────────────────────────────────────────────


@router.get("/groups", response_model=WhatsAppGroupListResponse)
async def list_whatsapp_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppGroupListResponse:
    """Get the list of WhatsApp groups from the sidebar.

    This triggers a one-shot Playwright scrape to fetch group names.
    For a faster response, it returns cached groups from the DB if available,
    otherwise scrapes fresh data.
    """
    import asyncio

    # Check if we have an active session
    result = await db.execute(
        select(WhatsAppSession)
        .filter(WhatsAppSession.is_active == True)
        .order_by(WhatsAppSession.id.desc())
        .limit(1)
    )
    session = result.scalars().first()

    if not session or session.status != "connected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WhatsApp is not connected. Please connect first.",
        )

    storage_state = session.storage_state_json
    if not storage_state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No stored session found. Please reconnect WhatsApp.",
        )

    # Scrape groups via Playwright
    try:
        from services.whatsapp_browser import (
            launch_whatsapp_browser,
            navigate_to_whatsapp,
            is_logged_in,
            fetch_group_list,
            safe_close,
        )

        pw, context, page = await launch_whatsapp_browser(
            headless=True, storage_state=storage_state
        )
        try:
            await navigate_to_whatsapp(page)

            if not await is_logged_in(page):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="WhatsApp session expired. Please reconnect.",
                )

            groups = await fetch_group_list(page)
        finally:
            await safe_close(pw, context)

        return WhatsAppGroupListResponse(
            groups=[WhatsAppGroupItem(**g) for g in groups]
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch groups: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch groups: {str(e)}",
        )


# ── POST /groups/select ──────────────────────────────────────────────────────


@router.post("/groups/select", response_model=WhatsAppGroupSelectResponse)
async def select_whatsapp_groups(
    payload: WhatsAppGroupSelectRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppGroupSelectResponse:
    """Save the 3 monitored groups and 1 forward group.

    Enforces exactly 3 monitored groups.
    """
    if len(payload.monitored_group_names) != 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Exactly 3 monitored groups must be selected.",
        )

    # Clear existing monitored groups
    from sqlalchemy import delete as sa_delete

    await db.execute(sa_delete(WhatsAppMonitoredGroup))
    await db.execute(sa_delete(WhatsAppForwardGroup))

    # Save monitored groups
    for name, gid in zip(
        payload.monitored_group_names, payload.monitored_group_ids
    ):
        group = WhatsAppMonitoredGroup(group_name=name, whatsapp_id=gid)
        db.add(group)

    # Save forward group
    forward = WhatsAppForwardGroup(
        group_name=payload.forward_group_name,
        whatsapp_id=payload.forward_group_id,
    )
    db.add(forward)

    await db.commit()

    return WhatsAppGroupSelectResponse(
        message="Groups saved successfully.",
        monitored_groups=payload.monitored_group_names,
        forward_group=payload.forward_group_name,
    )


# ── GET /filters ─────────────────────────────────────────────────────────────


@router.get("/filters", response_model=WhatsAppScanFilterResponse)
async def get_whatsapp_filters(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Get the current scan filters."""
    result = await db.execute(
        select(WhatsAppScanFilter).order_by(WhatsAppScanFilter.id.desc()).limit(1)
    )
    filters = result.scalars().first()

    if not filters:
        # Return defaults
        return WhatsAppScanFilterResponse(
            id=0,
            role=None,
            job_title=None,
            keywords=None,
            experience_level=None,
            match_threshold=60.0,
            updated_at=None,
        )

    return WhatsAppScanFilterResponse.model_validate(filters)


# ── POST /filters ────────────────────────────────────────────────────────────


@router.post("/filters", response_model=WhatsAppScanFilterResponse)
async def save_whatsapp_filters(
    payload: WhatsAppScanFilterRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Save scan filters (upsert)."""
    from datetime import datetime, timezone

    result = await db.execute(
        select(WhatsAppScanFilter).order_by(WhatsAppScanFilter.id.desc()).limit(1)
    )
    filters = result.scalars().first()

    if filters:
        # Update existing
        filters.role = payload.role
        filters.job_title = payload.job_title
        filters.keywords = payload.keywords
        filters.experience_level = payload.experience_level
        filters.match_threshold = payload.match_threshold
        filters.updated_at = datetime.now(timezone.utc)
    else:
        # Create new
        filters = WhatsAppScanFilter(
            role=payload.role,
            job_title=payload.job_title,
            keywords=payload.keywords,
            experience_level=payload.experience_level,
            match_threshold=payload.match_threshold,
        )
        db.add(filters)

    await db.commit()
    await db.refresh(filters)

    return WhatsAppScanFilterResponse.model_validate(filters)


# ── GET /messages ────────────────────────────────────────────────────────────


@router.get("/messages", response_model=WhatsAppMessageListResponse)
async def list_whatsapp_messages(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: str | None = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppMessageListResponse:
    """Get paginated list of scraped messages with scores and statuses."""
    query = select(WhatsAppRawMessage)

    if status_filter:
        query = query.where(WhatsAppRawMessage.status == status_filter)

    # Get total count
    count_query = select(sa_func.count()).select_from(
        query.subquery() if status_filter else WhatsAppRawMessage
    )
    if status_filter and not query.whereclause:
        count_query = select(sa_func.count()).select_from(WhatsAppRawMessage)

    # Simpler approach: two queries
    total_result = await db.execute(
        select(sa_func.count()).select_from(WhatsAppRawMessage).where(
            WhatsAppRawMessage.status == status_filter
        )
        if status_filter
        else select(sa_func.count()).select_from(WhatsAppRawMessage)
    )
    total = total_result.scalar() or 0

    # Fetch page
    query = (
        select(WhatsAppRawMessage)
        .order_by(WhatsAppRawMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if status_filter:
        query = query.where(WhatsAppRawMessage.status == status_filter)

    result = await db.execute(query)
    messages = result.scalars().all()

    return WhatsAppMessageListResponse(
        messages=[WhatsAppMessageResponse.model_validate(m) for m in messages],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── POST /scan/trigger ───────────────────────────────────────────────────────


@router.post("/scan/trigger", status_code=200)
async def trigger_whatsapp_scan(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a WhatsApp message scan."""
    from worker.celery_app import celery_app

    # Verify we have an active session
    result = await db.execute(
        select(WhatsAppSession)
        .filter(WhatsAppSession.is_active == True)
        .order_by(WhatsAppSession.id.desc())
        .limit(1)
    )
    session = result.scalars().first()

    if not session or session.status != "connected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="WhatsApp is not connected. Please connect first.",
        )

    celery_app.send_task("tasks.check_whatsapp_messages", countdown=2)

    return {"message": "WhatsApp scan triggered. Results will be available shortly."}


# ── GET /stats ───────────────────────────────────────────────────────────────


@router.get("/stats", response_model=WhatsAppStatsResponse)
async def get_whatsapp_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppStatsResponse:
    """Get aggregated counts: matched, rejected, forwarded, pending, total."""
    total_result = await db.execute(
        select(sa_func.count()).select_from(WhatsAppRawMessage)
    )
    total = total_result.scalar() or 0

    async def _count(status_val: str) -> int:
        r = await db.execute(
            select(sa_func.count())
            .select_from(WhatsAppRawMessage)
            .where(WhatsAppRawMessage.status == status_val)
        )
        return r.scalar() or 0

    matched = await _count("matched")
    rejected = await _count("rejected")
    pending = await _count("pending")
    forwarded_result = await db.execute(
        select(sa_func.count())
        .select_from(WhatsAppRawMessage)
        .where(WhatsAppRawMessage.forwarded == True)
    )
    forwarded = forwarded_result.scalar() or 0

    return WhatsAppStatsResponse(
        matched_count=matched,
        rejected_count=rejected,
        forwarded_count=forwarded,
        pending_count=pending,
        total_count=total,
    )

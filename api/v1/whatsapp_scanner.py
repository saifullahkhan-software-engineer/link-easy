"""
WhatsApp Job Scanner — API endpoints.
FILE: api/v1/whatsapp_scanner.py

POST   /api/v1/whatsapp/connect          → start embedded browser view + QR watcher
GET    /api/v1/whatsapp/status           → connection status
GET    /api/v1/whatsapp/groups           → list all groups
POST   /api/v1/whatsapp/groups/select    → save monitored + forward groups
GET    /api/v1/whatsapp/filters/jobs    → list the user's filter jobs
POST   /api/v1/whatsapp/filters/jobs    → create a draft filter job
GET    /api/v1/whatsapp/filters/jobs/{id} → filter details
PATCH/DELETE/POST .../{id}              → edit, remove, activate or pause
GET    /api/v1/whatsapp/filters         → legacy singleton filter endpoint
POST   /api/v1/whatsapp/filters         → legacy filter upsert
GET    /api/v1/whatsapp/messages         → paginated list with scores/status
POST   /api/v1/whatsapp/scan/trigger     → manually trigger scan task
GET    /api/v1/whatsapp/stats            → matched/rejected/forwarded counts

Connecting: instead of queueing a Celery task that opens a non-headless
browser on the server's display (invisible to the user), ``POST /connect``
launches the in-process headless browser view (services/browser_view.py) and
streams it into the WhatsApp Scanner page, then a background asyncio task
watches for the QR scan and persists the session state.

The browser is ONLY opened when needed for:
1. QR code scanning to connect WhatsApp
2. 2FA code entry (if required after QR scan)

After successful connection, the browser is stopped to free resources.
Logs are written to the terminal/backend for easy monitoring.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func as sa_func, update as sa_update

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
    WhatsAppScanFilterCreate,
    WhatsAppScanFilterUpdate,
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

router = APIRouter(prefix="/api/v1/whatsapp", tags=["whatsapp-scanner"])


async def _load_owned_filter(
    filter_id: int,
    current_user: User,
    db: AsyncSession,
) -> WhatsAppScanFilter:
    """Load a filter job owned by the caller or raise a uniform 404."""
    result = await db.execute(
        select(WhatsAppScanFilter).where(
            WhatsAppScanFilter.id == filter_id,
            WhatsAppScanFilter.owner_email == current_user.email,
        )
    )
    filter_row = result.scalars().first()
    if not filter_row:
        raise HTTPException(status_code=404, detail="WhatsApp filter not found")
    return filter_row


async def _filter_response(
    filter_row: WhatsAppScanFilter,
    db: AsyncSession,
) -> WhatsAppScanFilterResponse:
    """Build the list/detail shape including groups and message counters."""
    monitored_result = await db.execute(
        select(WhatsAppMonitoredGroup)
        .where(WhatsAppMonitoredGroup.filter_id == filter_row.id)
        .order_by(WhatsAppMonitoredGroup.id)
    )
    monitored = monitored_result.scalars().all()

    forward_result = await db.execute(
        select(WhatsAppForwardGroup)
        .where(WhatsAppForwardGroup.filter_id == filter_row.id)
        .order_by(WhatsAppForwardGroup.id)
        .limit(1)
    )
    forward = forward_result.scalars().first()

    total_result = await db.execute(
        select(sa_func.count())
        .select_from(WhatsAppRawMessage)
        .where(WhatsAppRawMessage.filter_id == filter_row.id)
    )
    total = total_result.scalar() or 0

    async def _count(status_value: str) -> int:
        result = await db.execute(
            select(sa_func.count())
            .select_from(WhatsAppRawMessage)
            .where(
                WhatsAppRawMessage.filter_id == filter_row.id,
                WhatsAppRawMessage.status == status_value,
            )
        )
        return result.scalar() or 0

    forwarded_result = await db.execute(
        select(sa_func.count())
        .select_from(WhatsAppRawMessage)
        .where(
            WhatsAppRawMessage.filter_id == filter_row.id,
            WhatsAppRawMessage.forwarded == True,
        )
    )

    return WhatsAppScanFilterResponse(
        id=filter_row.id,
        name=filter_row.name or "WhatsApp Filter",
        owner_email=filter_row.owner_email,
        status=filter_row.status or "draft",
        role=filter_row.role,
        job_title=filter_row.job_title,
        keywords=filter_row.keywords,
        experience_level=filter_row.experience_level,
        match_threshold=filter_row.match_threshold,
        interval_hours=filter_row.interval_hours,
        latest_messages_limit=filter_row.latest_messages_limit,
        remaining_seconds=filter_row.remaining_seconds,
        next_scan_at=filter_row.next_scan_at,
        updated_at=filter_row.updated_at,
        created_at=getattr(filter_row, "created_at", None),
        last_scan_at=filter_row.last_scan_at,
        monitored_group_names=[group.group_name for group in monitored],
        monitored_groups=monitored,
        forward_group_name=forward.group_name if forward else None,
        forward_group=forward,
        total_count=total,
        matched_count=await _count("matched"),
        rejected_count=await _count("rejected"),
        forwarded_count=forwarded_result.scalar() or 0,
    )


# ── QR watcher bookkeeping ───────────────────────────────────────────────────

# Session ids that currently have a live ``_watch_qr_scan`` task.  Used by
# ``/connect`` to tell a truly in-progress connection apart from a stale
# ``waiting_qr`` row whose watcher died (timeout / server restart / browser
# stop) — stale rows used to leave the UI stuck on the QR screen forever.
_active_watchers: set[int] = set()

# Serializes WhatsApp browser operations inside the API process. Two browsers
# on the same WhatsApp account at once (the live browser view + a group-fetch
# browser) is exactly what used to break freshly-scanned connections; the
# redis profile lock additionally coordinates with the Celery worker.
_whatsapp_op_lock = asyncio.Lock()


def _watcher_running(session_id: int) -> bool:
    return session_id in _active_watchers


def _spawn_qr_watcher(session_id: int, max_wait_seconds: int = 300) -> None:
    """Create the background QR-watch task (idempotent per session)."""
    if session_id in _active_watchers:
        return
    _active_watchers.add(session_id)
    task = asyncio.create_task(_watch_qr_scan(session_id, max_wait_seconds))
    task.add_done_callback(lambda _t: _active_watchers.discard(session_id))


# ── POST /connect ────────────────────────────────────────────────────────────


async def _watch_qr_scan(session_id: int, max_wait_seconds: int = 300) -> None:
    """Background task: wait for the QR code in the embedded browser view.

    Polls the live browser page until WhatsApp Web shows the chat list
    (i.e. the QR was scanned), then persists cookies/storage state and marks
    the session connected.  On timeout the session is marked disconnected.

    After successful login, the browser is stopped to free resources.
    If 2FA is needed, the browser remains open for the user to enter the code.
    Once 2FA is completed (logged in), the browser is stopped.
    """
    from services.browser_view import browser_view
    from services.whatsapp_browser import get_storage_state, is_logged_in
    from database import async_session

    logger.info("👀 Watching for WhatsApp QR scan (session id=%s)", session_id)

    deadline = time.monotonic() + max_wait_seconds
    logged_in = False
    encountered_2fa = False
    two_fa_completed = False
    browser_aborted = False
    while time.monotonic() < deadline:
        page = browser_view.page
        if page is None or browser_view.status not in ("running", "starting"):
            logger.warning("Browser view stopped while waiting for QR — aborting watch")
            browser_aborted = True
            break
        try:
            if await is_logged_in(page):
                # Give WhatsApp a moment to finish syncing and flush the
                # session keys before we snapshot the storage state, then
                # re-verify so a transient frame can't trigger a save.
                await asyncio.sleep(3)
                if await is_logged_in(page):
                    logged_in = True
                    if encountered_2fa:
                        two_fa_completed = True
                        logger.info("✅ 2FA completed — WhatsApp logged in successfully")
                    break
                continue

            # Check for 2FA page
            if await _check_2fa_page(page):
                if not encountered_2fa:
                    encountered_2fa = True
                    logger.info("🔐 2FA required — keeping browser open for code entry")
                    logger.info("📝 User must enter the 6-digit code in the browser view")
                # Keep browser open for 2FA - don't break, continue waiting
                # The user needs to enter the code manually via the browser view
        except Exception as exc:  # page may be mid-navigation
            logger.debug("QR check error: %s", exc)
        await asyncio.sleep(2)

    storage_state = None
    if logged_in:
        logger.info("✅ QR code scanned — extracting session state…")
        try:
            storage_state = await get_storage_state(browser_view.context)
        except Exception as exc:
            logger.error("Could not extract storage state: %s", exc)
            storage_state = None

    async with async_session() as db:
        row = (
            await db.execute(
                select(WhatsAppSession).where(WhatsAppSession.id == session_id)
            )
        ).scalar_one_or_none()
        if row:
            if storage_state is not None:
                row.storage_state_json = storage_state
                row.cookies_json = storage_state.get("cookies", [])
                row.status = "connected"
                row.is_active = True
                row.updated_at = datetime.now(timezone.utc)
                message = "✅ WhatsApp connected — session state saved"
            else:
                row.status = "error" if logged_in else "disconnected"
                row.is_active = False
                message = (
                    "❌ WhatsApp connect failed — could not save session state"
                    if logged_in
                    else "⏰ QR scan timed out — connection marked disconnected"
                )
        else:
            message = f"ℹ️ Session {session_id} no longer exists — watcher exiting"
        await db.commit()

    logger.info(message)

    # Stop the browser when the flow is over:
    # - After successful login (with or without 2FA)
    # - On QR timeout (fresh QR on next Connect) or if the browser died
    # Only the 2FA-awaiting-PIN case keeps the browser open.
    if logged_in:
        if two_fa_completed:
            logger.info("🛑 Stopping browser view after 2FA completion")
        else:
            logger.info("🛑 Stopping browser view after successful WhatsApp connection")
        try:
            await browser_view.stop()
        except Exception as exc:
            logger.warning("Error stopping browser view: %s", exc)
    elif encountered_2fa:
        # 2FA was needed but we timed out waiting for completion
        logger.warning("⏰ 2FA code entry timed out — browser will remain open")
        logger.info("💡 User can manually stop the browser via the UI or API")
    elif not browser_aborted:
        # Timed out waiting for the scan — leave a clean slate so the next
        # Connect starts a fresh QR with a fresh watcher (previously the
        # browser stayed open on a live QR nobody was watching, so further
        # scans never completed and the screen looked "stuck").
        logger.info("🛑 Stopping browser view after QR timeout — press Connect for a fresh QR")
        try:
            await browser_view.stop()
        except Exception as exc:
            logger.warning("Error stopping browser view: %s", exc)


async def _check_2fa_page(page) -> bool:
    """Check if the current page is asking for 2FA (two-factor authentication).
    
    Returns True if 2FA is required.
    """
    try:
        # Check for 2FA input field - WhatsApp Web shows a screen to enter the 6-digit code
        content = await page.content()
        
        # Look for 2FA indicators — WhatsApp's actual two-step verification
        # screen says "Two-Step Verification" / "Enter your PIN", so include
        # the real copy (the old list only matched generic phrases and
        # silently missed real 2FA screens, leaving scans stuck).
        two_fa_indicators = [
            "two-step verification",
            "two-factor authentication",
            "enter your pin",
            "enter the 6-digit",
            "enter the code",
            "authentication code",
            "enter-manual-code",
        ]
        
        for indicator in two_fa_indicators:
            if indicator.lower() in content.lower():
                return True
        
        # Also check via selector for 2FA input
        two_fa_input = await page.query_selector(
            'input[data-testid="enter-manual-code"], input[type="text"], input[inputmode="numeric"]'
        )
        if two_fa_input:
            # Check if it's likely a 2FA input by looking at nearby text
            parent = await two_fa_input.evaluate("el => el.parentElement?.parentElement?.parentElement")
            if parent:
                parent_text = await page.evaluate(
                    "el => el.textContent", parent
                )
                if "code" in parent_text.lower() or "2fa" in parent_text.lower() or "authentication" in parent_text.lower():
                    return True
            
    except Exception as e:
        logger.debug("Error checking for 2FA page: %s", e)
    
    return False


@router.post("/connect", response_model=WhatsAppConnectResponse)
async def connect_whatsapp(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppConnectResponse:
    """Launch the embedded browser view for WhatsApp QR login.

    The browser runs headless inside the API process and is streamed to the
    page via ``/api/v1/live/browser/stream``; a background watcher saves the
    session state once the QR code is scanned.

    The browser is ONLY opened for QR scan and 2FA entry. After successful
    connection, the browser is stopped. Logs are written to the terminal.
    """
    from services.browser_view import WHATSAPP_URL, browser_view

    # Check if there's already a connection in progress
    result = await db.execute(
        select(WhatsAppSession).order_by(WhatsAppSession.id.desc()).limit(1)
    )
    existing = result.scalars().first()

    if existing and existing.status == "waiting_qr":
        if _watcher_running(existing.id):
            logger.info("📱 WhatsApp connection already in progress (session id=%s)", existing.id)
            return WhatsAppConnectResponse(
                message="A WhatsApp connection is already in progress — scan the QR code in the Live Browser view.",
                status="waiting_qr",
            )
        # Stale "waiting_qr" row: the record exists but nothing is watching
        # (previous watcher timed out, server restarted, or the browser was
        # stopped).  Restart the browser view + watcher for the SAME session
        # so the user always has a way out of the "stuck on QR" state.
        logger.info(
            "📱 Restarting stale WhatsApp connection (session id=%s) — no live watcher",
            existing.id,
        )
        start_result = await browser_view.ensure_started(WHATSAPP_URL)
        if start_result.get("status") == "error":
            existing.status = "error"
            existing.is_active = False
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not open the browser view: {start_result.get('error')}",
            )
        _spawn_qr_watcher(existing.id)
        return WhatsAppConnectResponse(
            message="Restarted the WhatsApp connection — scan the new QR code in the Live Browser view.",
            status="waiting_qr",
        )

    # Deactivate any existing sessions
    if existing:
        existing.is_active = False
        await db.commit()

    # Create a new pending session record
    new_session = WhatsAppSession(status="waiting_qr", is_active=True)
    db.add(new_session)
    await db.commit()
    await db.refresh(new_session)

    # Launch (or reuse) the embedded headless browser on WhatsApp Web.
    logger.info("📱 Starting browser view for WhatsApp QR scan (session id=%s)", new_session.id)
    start_result = await browser_view.ensure_started(WHATSAPP_URL)
    if start_result.get("status") == "error":
        new_session.status = "error"
        new_session.is_active = False
        await db.commit()
        logger.error(
            "📱 WhatsApp connect failed to open browser view: %s",
            start_result.get("error"),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "Could not open the browser view: "
                f"{start_result.get('error')}. "
                "Check that Chromium is installed and the patchright browser "
                "binaries are present."
            ),
        )

    # Watch for the QR scan in the background (no Celery needed).
    _spawn_qr_watcher(new_session.id)

    logger.info("📱 WhatsApp connect started — scan QR code in browser view (session id=%s)", new_session.id)
    return WhatsAppConnectResponse(
        message="WhatsApp connection started — scan the QR code in the Live Browser view below.",
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
    search: str | None = None,
    filter_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppGroupListResponse:
    """Get the list of WhatsApp groups from the sidebar.

    Runs against the durable WhatsApp profile. If the live browser view is
    already open it is REUSED — launching a second browser on the same
    account while one is running is exactly what used to break freshly
    scanned connections. Otherwise a headless persistent-context browser is
    launched under the profile lock.

    A slow-loading page is never treated as an expired session: we wait up to
    30s for the chat list and only mark the session disconnected when the QR
    landing screen is actually confirmed.
    """
    if filter_id is not None:
        await _load_owned_filter(filter_id, current_user, db)

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

    from services.browser_view import browser_view
    from services.whatsapp_browser import (
        fetch_group_list,
        is_showing_qr,
        launch_whatsapp_persistent,
        navigate_to_whatsapp,
        safe_close,
        wait_for_login,
    )
    from worker.profile_lock import (
        ProfileInUseError,
        acquire_profile_lock,
        release_profile_lock,
    )

    async with _whatsapp_op_lock:
        pw = None
        context = None
        page = None
        profile_lock = None
        try:
            # 1) Reuse the live browser view when it is already running on
            #    WhatsApp — no second browser, no session conflict.
            if browser_view.status == "running" and browser_view.page is not None:
                try:
                    live_page = browser_view.page
                    if "web.whatsapp.com" not in (live_page.url or ""):
                        await navigate_to_whatsapp(live_page)
                    page = live_page
                except Exception as exc:
                    logger.warning("Live browser view page unusable (%s) — launching a fresh browser", exc)
                    page = None

            # 2) Otherwise launch the persistent-profile browser ourselves.
            if page is None:
                try:
                    profile_lock = acquire_profile_lock("whatsapp", blocking_timeout=20)
                except ProfileInUseError:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            "The WhatsApp browser is busy with another operation "
                            "(e.g. a scan). Please try again in a few seconds."
                        ),
                    )
                try:
                    pw, context, page = await launch_whatsapp_persistent(headless=True)
                except Exception as exc:
                    logger.error("Failed to launch WhatsApp browser: %s", exc)
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Could not open the WhatsApp browser: {exc}",
                    )
                await navigate_to_whatsapp(page)

            # Give WhatsApp Web real time to finish loading before deciding
            # anything about the session.
            if not await wait_for_login(page, timeout_seconds=30):
                if await is_showing_qr(page):
                    session.status = "disconnected"
                    session.is_active = False
                    await db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="WhatsApp session expired. Please reconnect.",
                    )
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="WhatsApp Web did not finish loading in time. Please try again.",
                )

            groups = await fetch_group_list(page, search=search.strip() if search else None)
        finally:
            # Only close browsers we launched ourselves — never the live view.
            if context is not None:
                await safe_close(pw, context)
            if profile_lock is not None:
                release_profile_lock(profile_lock)

    try:
        monitored_query = select(WhatsAppMonitoredGroup).order_by(WhatsAppMonitoredGroup.id)
        forward_query = select(WhatsAppForwardGroup).order_by(WhatsAppForwardGroup.id).limit(1)
        if filter_id is not None:
            monitored_query = monitored_query.where(WhatsAppMonitoredGroup.filter_id == filter_id)
            forward_query = forward_query.where(WhatsAppForwardGroup.filter_id == filter_id)
        else:
            # Legacy singleton rows are deliberately NULL-scoped.
            monitored_query = monitored_query.where(WhatsAppMonitoredGroup.filter_id.is_(None))
            forward_query = forward_query.where(WhatsAppForwardGroup.filter_id.is_(None))

        monitored_result = await db.execute(monitored_query)
        forward_result = await db.execute(forward_query)
        saved_forward = forward_result.scalars().first()
        return WhatsAppGroupListResponse(
            groups=[WhatsAppGroupItem(**g) for g in groups],
            monitored_group_names=[g.group_name for g in monitored_result.scalars().all()],
            forward_group_name=saved_forward.group_name if saved_forward else None,
        )
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
    """Save one to three monitored groups and one forwarding group.

    Existing monitored-group rows are reconciled instead of deleted and
    recreated.  This is important because each row stores the durable
    ``last_message_id`` cursor used to prevent later scans from walking back
    over messages that were already pulled.
    """
    if payload.filter_id is not None:
        await _load_owned_filter(payload.filter_id, current_user, db)

    # Normalise empty strings to None so DB stores NULL rather than ''.
    def _clean(value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned if cleaned else None

    try:
        group_scope = (
            WhatsAppMonitoredGroup.filter_id == payload.filter_id
            if payload.filter_id is not None
            else WhatsAppMonitoredGroup.filter_id.is_(None)
        )
        forward_scope = (
            WhatsAppForwardGroup.filter_id == payload.filter_id
            if payload.filter_id is not None
            else WhatsAppForwardGroup.filter_id.is_(None)
        )

        existing_result = await db.execute(
            select(WhatsAppMonitoredGroup)
            .where(group_scope)
            .order_by(WhatsAppMonitoredGroup.id)
        )
        existing_groups = list(existing_result.scalars().all())
        unused_groups = list(existing_groups)

        # Prefer a stable WhatsApp id, then fall back to the saved group name.
        # Keeping the same ORM row keeps its last-message checkpoint intact.
        for raw_name, raw_gid in zip(
            payload.monitored_group_names, payload.monitored_group_ids
        ):
            name = raw_name.strip()
            gid = _clean(raw_gid)
            matched = next(
                (group for group in unused_groups if gid and group.whatsapp_id == gid),
                None,
            )
            if matched is None:
                matched = next(
                    (
                        group
                        for group in unused_groups
                        if group.group_name.casefold() == name.casefold()
                    ),
                    None,
                )

            if matched is None:
                db.add(
                    WhatsAppMonitoredGroup(
                        filter_id=payload.filter_id,
                        group_name=name,
                        whatsapp_id=gid,
                    )
                )
            else:
                matched.group_name = name
                # A placeholder selected while WhatsApp is disconnected has no
                # id; do not erase the previously saved stable id in that case.
                if gid is not None:
                    matched.whatsapp_id = gid
                unused_groups.remove(matched)

        for removed_group in unused_groups:
            await db.delete(removed_group)

        forward_result = await db.execute(
            select(WhatsAppForwardGroup)
            .where(forward_scope)
            .order_by(WhatsAppForwardGroup.id)
        )
        existing_forward_groups = list(forward_result.scalars().all())
        if existing_forward_groups:
            forward = existing_forward_groups[0]
            forward.group_name = payload.forward_group_name.strip()
            cleaned_forward_id = _clean(payload.forward_group_id)
            if cleaned_forward_id is not None:
                forward.whatsapp_id = cleaned_forward_id
            for duplicate_forward in existing_forward_groups[1:]:
                await db.delete(duplicate_forward)
        else:
            forward = WhatsAppForwardGroup(
                filter_id=payload.filter_id,
                group_name=payload.forward_group_name.strip(),
                whatsapp_id=_clean(payload.forward_group_id),
            )
            db.add(forward)

        await db.commit()
        logger.info(
            "💾 Saved monitored groups=%s forward=%s (scan cursors preserved)",
            payload.monitored_group_names,
            payload.forward_group_name,
        )
    except Exception as exc:
        await db.rollback()
        logger.error("Failed to save WhatsApp groups: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save groups: {exc}",
        )

    return WhatsAppGroupSelectResponse(
        message="Groups saved successfully.",
        monitored_groups=payload.monitored_group_names,
        forward_group=payload.forward_group_name,
    )


# ── Filter jobs workflow ─────────────────────────────────────────────────────


@router.get("/filters/jobs", response_model=list[WhatsAppScanFilterResponse])
async def list_whatsapp_filter_jobs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[WhatsAppScanFilterResponse]:
    """List the caller's WhatsApp filter jobs, newest first."""
    result = await db.execute(
        select(WhatsAppScanFilter)
        .where(
            (WhatsAppScanFilter.owner_email == current_user.email)
            | (WhatsAppScanFilter.owner_email.is_(None))
        )
        .order_by(WhatsAppScanFilter.created_at.desc(), WhatsAppScanFilter.id.desc())
    )
    rows = result.scalars().all()

    # Adopt the one legacy singleton row into the authenticated user's filter
    # workspace on first visit. Move its NULL-scoped groups/results as well so
    # the new detail page shows the existing configuration instead of looking
    # empty after the migration.
    adopted = False
    for row in rows:
        if row.owner_email is not None:
            continue
        row.owner_email = current_user.email
        row.name = row.name or "WhatsApp Filter"
        row.status = row.status or "active"
        await db.execute(
            sa_update(WhatsAppMonitoredGroup)
            .where(WhatsAppMonitoredGroup.filter_id.is_(None))
            .values(filter_id=row.id)
        )
        await db.execute(
            sa_update(WhatsAppForwardGroup)
            .where(WhatsAppForwardGroup.filter_id.is_(None))
            .values(filter_id=row.id)
        )
        await db.execute(
            sa_update(WhatsAppRawMessage)
            .where(WhatsAppRawMessage.filter_id.is_(None))
            .values(filter_id=row.id)
        )
        adopted = True
    if adopted:
        await db.commit()
        # Bulk UPDATE statements can expire scalar attributes on ORM rows even
        # when the session uses expire_on_commit=False. Refresh before building
        # the response so async SQLAlchemy never attempts implicit IO.
        for row in rows:
            await db.refresh(row)

    return [await _filter_response(row, db) for row in rows]


@router.post(
    "/filters/jobs",
    response_model=WhatsAppScanFilterResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_whatsapp_filter_job(
    payload: WhatsAppScanFilterCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Create a draft WhatsApp filter job."""
    filter_row = WhatsAppScanFilter(
        owner_email=current_user.email,
        name=payload.name.strip(),
        status="draft",
        role=payload.role,
        job_title=payload.job_title,
        keywords=payload.keywords,
        experience_level=payload.experience_level,
        match_threshold=payload.match_threshold,
        interval_hours=payload.interval_hours,
        latest_messages_limit=payload.latest_messages_limit,
    )
    db.add(filter_row)
    await db.commit()
    await db.refresh(filter_row)
    return await _filter_response(filter_row, db)


@router.get("/filters/{filter_id}", response_model=WhatsAppScanFilterResponse)
@router.get("/filters/jobs/{filter_id}", response_model=WhatsAppScanFilterResponse)
async def get_whatsapp_filter_job(
    filter_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Return all configuration and counters for one filter job."""
    filter_row = await _load_owned_filter(filter_id, current_user, db)
    return await _filter_response(filter_row, db)


@router.patch("/filters/{filter_id}", response_model=WhatsAppScanFilterResponse)
@router.patch("/filters/jobs/{filter_id}", response_model=WhatsAppScanFilterResponse)
async def update_whatsapp_filter_job(
    filter_id: int,
    payload: WhatsAppScanFilterUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Update filter criteria without changing its lifecycle state."""
    filter_row = await _load_owned_filter(filter_id, current_user, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "name" and value is not None:
            value = value.strip()
        setattr(filter_row, field, value)
    filter_row.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(filter_row)
    return await _filter_response(filter_row, db)


@router.delete("/filters/{filter_id}", status_code=200)
@router.delete("/filters/jobs/{filter_id}", status_code=200)
async def delete_whatsapp_filter_job(
    filter_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a filter and its group configuration/results."""
    from sqlalchemy import delete as sa_delete

    filter_row = await _load_owned_filter(filter_id, current_user, db)
    filter_name = filter_row.name
    await db.execute(
        sa_delete(WhatsAppRawMessage).where(WhatsAppRawMessage.filter_id == filter_id)
    )
    await db.execute(
        sa_delete(WhatsAppMonitoredGroup).where(
            WhatsAppMonitoredGroup.filter_id == filter_id
        )
    )
    await db.execute(
        sa_delete(WhatsAppForwardGroup).where(
            WhatsAppForwardGroup.filter_id == filter_id
        )
    )
    await db.delete(filter_row)
    await db.commit()
    return {"message": f"WhatsApp filter '{filter_name}' deleted successfully"}


@router.post("/filters/{filter_id}/activate", status_code=200)
@router.post("/filters/jobs/{filter_id}/activate", status_code=200)
async def activate_whatsapp_filter_job(
    filter_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start or resume a filter job, preserving a paused countdown."""
    from worker.celery_app import celery_app

    filter_row = await _load_owned_filter(filter_id, current_user, db)
    if filter_row.status == "active":
        raise HTTPException(status_code=409, detail="Filter is already active")

    monitored_result = await db.execute(
        select(WhatsAppMonitoredGroup.id)
        .where(WhatsAppMonitoredGroup.filter_id == filter_id)
        .limit(1)
    )
    if monitored_result.scalar() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one monitored group before starting this filter.",
        )

    forward_result = await db.execute(
        select(WhatsAppForwardGroup.id)
        .where(WhatsAppForwardGroup.filter_id == filter_id)
        .limit(1)
    )
    if forward_result.scalar() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a forwarding group before starting this filter.",
        )

    now = datetime.now(timezone.utc)
    filter_row.status = "active"
    if filter_row.remaining_seconds is not None and filter_row.remaining_seconds > 0:
        delay_seconds = filter_row.remaining_seconds
        filter_row.next_scan_at = now + timedelta(seconds=delay_seconds)
        filter_row.remaining_seconds = None
        message = f"Filter '{filter_row.name}' resumed. Next scan in {delay_seconds} seconds."
    else:
        delay_seconds = 10
        filter_row.next_scan_at = now + timedelta(seconds=delay_seconds)
        filter_row.remaining_seconds = None
        message = f"Filter '{filter_row.name}' activated. First scan starting..."

    next_scan_at = filter_row.next_scan_at
    job_id = filter_row.id
    await db.commit()
    celery_app.send_task(
        "tasks.check_whatsapp_messages",
        args=[job_id],
        countdown=max(5, delay_seconds),
    )
    return {"message": message, "next_scan_at": next_scan_at}


@router.post("/filters/{filter_id}/pause", status_code=200)
@router.post("/filters/jobs/{filter_id}/pause", status_code=200)
async def pause_whatsapp_filter_job(
    filter_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pause a running filter and preserve its remaining scan time."""
    filter_row = await _load_owned_filter(filter_id, current_user, db)
    if filter_row.status != "active":
        raise HTTPException(status_code=400, detail="Filter is not active")

    now = datetime.now(timezone.utc)
    if filter_row.next_scan_at:
        next_at = filter_row.next_scan_at
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        filter_row.remaining_seconds = max(0, int((next_at - now).total_seconds()))
    else:
        filter_row.remaining_seconds = max(0, int(filter_row.interval_hours * 3600))
    filter_row.status = "paused"
    remaining_seconds = filter_row.remaining_seconds
    filter_name = filter_row.name
    await db.commit()
    return {
        "message": f"Filter '{filter_name}' paused",
        "remaining_seconds": remaining_seconds,
    }


# ── GET /filters ─────────────────────────────────────────────────────────────
# Legacy singleton endpoints remain available for older clients. New pages use
# /filters/jobs above and never mix their data with the legacy NULL-scoped rows.


@router.get("/filters", response_model=WhatsAppScanFilterResponse)
async def get_whatsapp_filters(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppScanFilterResponse:
    """Get the current scan filters."""
    result = await db.execute(
        select(WhatsAppScanFilter)
        .where(
            (WhatsAppScanFilter.owner_email == current_user.email)
            | (WhatsAppScanFilter.owner_email.is_(None))
        )
        .order_by(WhatsAppScanFilter.id.desc())
        .limit(1)
    )
    filters = result.scalars().first()

    if not filters:
        # Return defaults — must match the response schema (updated_at optional)
        return WhatsAppScanFilterResponse(
            id=0,
            role=None,
            job_title=None,
            keywords=None,
            experience_level=None,
            match_threshold=60.0,
            interval_hours=1.0,
            updated_at=None,
            last_scan_at=None,
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
        select(WhatsAppScanFilter)
        .where(
            (WhatsAppScanFilter.owner_email == current_user.email)
            | (WhatsAppScanFilter.owner_email.is_(None))
        )
        .order_by(WhatsAppScanFilter.id.desc())
        .limit(1)
    )
    filters = result.scalars().first()

    if filters:
        # Claim a legacy row when the first authenticated user saves it, then
        # keep the old singleton endpoint active for compatibility.
        if filters.owner_email is None:
            filters.owner_email = current_user.email
        filters.status = "active"
        filters.name = filters.name or "WhatsApp Filter"
        filters.role = payload.role
        filters.job_title = payload.job_title
        filters.keywords = payload.keywords
        filters.experience_level = payload.experience_level
        filters.match_threshold = payload.match_threshold
        filters.interval_hours = payload.interval_hours
        filters.latest_messages_limit = payload.latest_messages_limit
        filters.updated_at = datetime.now(timezone.utc)
    else:
        # Create new
        filters = WhatsAppScanFilter(
            owner_email=current_user.email,
            name="WhatsApp Filter",
            status="active",
            role=payload.role,
            job_title=payload.job_title,
            keywords=payload.keywords,
            experience_level=payload.experience_level,
            match_threshold=payload.match_threshold,
            interval_hours=payload.interval_hours,
            latest_messages_limit=payload.latest_messages_limit,
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
    filter_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppMessageListResponse:
    """Get paginated list of scraped messages with scores and statuses."""
    if filter_id is not None:
        await _load_owned_filter(filter_id, current_user, db)

    filters = [WhatsAppRawMessage.filter_id == filter_id] if filter_id is not None else []
    if status_filter:
        filters.append(WhatsAppRawMessage.status == status_filter)

    count_query = select(sa_func.count()).select_from(WhatsAppRawMessage)
    if filters:
        count_query = count_query.where(*filters)
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    query = (
        select(WhatsAppRawMessage)
        .order_by(WhatsAppRawMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if filters:
        query = query.where(*filters)

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
    filter_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger a WhatsApp message scan."""
    from worker.celery_app import celery_app

    if filter_id is not None:
        await _load_owned_filter(filter_id, current_user, db)

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

    # Pre-flight: ensure groups are configured, otherwise the Celery task
    # will immediately skip with "No monitored groups" and the user sees
    # nothing happening.
    monitored_query = select(WhatsAppMonitoredGroup).limit(1)
    forward_query = select(WhatsAppForwardGroup).limit(1)
    if filter_id is not None:
        monitored_query = monitored_query.where(WhatsAppMonitoredGroup.filter_id == filter_id)
        forward_query = forward_query.where(WhatsAppForwardGroup.filter_id == filter_id)
    else:
        monitored_query = monitored_query.where(WhatsAppMonitoredGroup.filter_id.is_(None))
        forward_query = forward_query.where(WhatsAppForwardGroup.filter_id.is_(None))

    mg_result = await db.execute(monitored_query)
    if mg_result.scalars().first() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No monitored groups configured. Please select and save at least one group first.",
        )

    fg_result = await db.execute(forward_query)
    if fg_result.scalars().first() is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No forward group configured. Please select a forward group.",
        )

    celery_app.send_task(
        "tasks.check_whatsapp_messages",
        args=[filter_id] if filter_id is not None else [],
        countdown=2,
    )
    logger.info(
        "📱 Manual WhatsApp scan triggered by user %s filter=%s",
        current_user.email,
        filter_id,
    )

    return {"message": "WhatsApp scan triggered. Results will be available shortly."}


# ── GET /stats ───────────────────────────────────────────────────────────────


@router.get("/stats", response_model=WhatsAppStatsResponse)
async def get_whatsapp_stats(
    filter_id: int | None = Query(None, ge=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WhatsAppStatsResponse:
    """Get aggregated counts: matched, rejected, forwarded, pending, total."""
    if filter_id is not None:
        await _load_owned_filter(filter_id, current_user, db)

    scope = [WhatsAppRawMessage.filter_id == filter_id] if filter_id is not None else []

    def _scoped(query):
        return query.where(*scope) if scope else query

    total_result = await db.execute(
        _scoped(select(sa_func.count()).select_from(WhatsAppRawMessage))
    )
    total = total_result.scalar() or 0

    async def _count(status_val: str) -> int:
        r = await db.execute(
            _scoped(
                select(sa_func.count())
                .select_from(WhatsAppRawMessage)
                .where(WhatsAppRawMessage.status == status_val)
            )
        )
        return r.scalar() or 0

    matched = await _count("matched")
    rejected = await _count("rejected")
    pending = await _count("pending")
    forwarded_result = await db.execute(
        _scoped(
            select(sa_func.count())
            .select_from(WhatsAppRawMessage)
            .where(WhatsAppRawMessage.forwarded == True)
        )
    )
    forwarded = forwarded_result.scalar() or 0

    return WhatsAppStatsResponse(
        matched_count=matched,
        rejected_count=rejected,
        forwarded_count=forwarded,
        pending_count=pending,
        total_count=total,
    )

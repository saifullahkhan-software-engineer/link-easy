"""
WhatsApp Job Scanner — Celery tasks.
FILE: worker/tasks/whatsapp_tasks.py

Tasks:
  connect_whatsapp     — Launch browser for QR login, save session.
  check_whatsapp_messages — Periodic: scrape groups, OCR images, score, forward.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.logging_config import get_logger
from worker.celery_app import celery_app

logger = get_logger(__name__)

# ── Sync DB session for Celery ──────────────────────────────────────────────
def _make_sync_url(async_url: str) -> str:
    """Convert async DATABASE_URL to sync psycopg2 URL for Celery tasks."""
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


@contextmanager
def get_sync_db():
    """Context manager for a sync SQLAlchemy session in Celery tasks."""
    session = SyncSession()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Task: Connect WhatsApp ───────────────────────────────────────────────────


@celery_app.task(bind=True, name="tasks.connect_whatsapp", max_retries=0)
def connect_whatsapp(self):
    logger.info("🚀 Starting WhatsApp connection task...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_connect_whatsapp_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"WhatsApp connect task failed: {e}")
        with get_sync_db() as db:
            from models.whatsapp import WhatsAppSession

            session_row = db.query(WhatsAppSession).order_by(
                WhatsAppSession.id.desc()
            ).first()
            if session_row:
                session_row.status = "error"
                session_row.is_active = False
        return {"status": "error", "message": str(e)}


async def _connect_whatsapp_async() -> dict:
    from services.whatsapp_browser import (
        launch_whatsapp_persistent,
        navigate_to_whatsapp,
        wait_for_qr_scan,
        get_storage_state,
        safe_close,
    )
    from worker.profile_lock import acquire_profile_lock, release_profile_lock

    profile_lock = acquire_profile_lock("whatsapp", blocking_timeout=10)
    try:
        pw, context, page = await launch_whatsapp_persistent(headless=False)
    except Exception:
        release_profile_lock(profile_lock)
        raise

    try:
        await navigate_to_whatsapp(page)

        with get_sync_db() as db:
            from models.whatsapp import WhatsAppSession

            session_row = db.query(WhatsAppSession).order_by(
                WhatsAppSession.id.desc()
            ).first()
            if not session_row:
                session_row = WhatsAppSession(status="waiting_qr", is_active=True)
                db.add(session_row)
                db.flush()
            else:
                session_row.status = "waiting_qr"
                session_row.is_active = True

        logged_in = await wait_for_qr_scan(page, max_wait_seconds=180)

        if not logged_in:
            with get_sync_db() as db:
                from models.whatsapp import WhatsAppSession

                session_row = db.query(WhatsAppSession).order_by(
                    WhatsAppSession.id.desc()
                ).first()
                if session_row:
                    session_row.status = "disconnected"
                    session_row.is_active = False
            return {"status": "timeout", "message": "QR scan timed out"}

        storage_state = await get_storage_state(context)
        cookies = storage_state.get("cookies", [])

        with get_sync_db() as db:
            from models.whatsapp import WhatsAppSession

            session_row = db.query(WhatsAppSession).order_by(
                WhatsAppSession.id.desc()
            ).first()
            if session_row:
                session_row.cookies_json = cookies
                session_row.storage_state_json = storage_state
                session_row.status = "connected"
                session_row.is_active = True
                session_row.updated_at = datetime.now(timezone.utc)

        logger.info("✅ WhatsApp connected — session saved to DB")
        return {"status": "connected", "message": "WhatsApp connected successfully"}

    finally:
        await safe_close(pw, context)
        release_profile_lock(profile_lock)


# ── Task: Periodic Message Check ─────────────────────────────────────────────


@celery_app.task(name="tasks.dispatch_due_whatsapp_scans")
def dispatch_due_whatsapp_scans() -> dict:
    """Queue active filter jobs whose next scan is due.

    The database timestamp is the source of truth, so pausing a job survives
    worker restarts and a queued task simply exits after checking its status.
    ``next_scan_at`` is advanced before dispatch to avoid duplicate queueing
    while a browser scan is still running.
    """
    now = datetime.now(timezone.utc)
    due_ids = []
    with get_sync_db() as db:
        from models.whatsapp import WhatsAppScanFilter

        due_filters = (
            db.query(WhatsAppScanFilter)
            .filter(
                WhatsAppScanFilter.status == "active",
                (
                    (WhatsAppScanFilter.next_scan_at == None)
                    | (WhatsAppScanFilter.next_scan_at <= now)
                ),
            )
            .all()
        )
        for filter_row in due_filters:
            # Reserve the next interval before putting work on the queue. The
            # worker writes the precise completion time when it finishes.
            filter_row.next_scan_at = now + timedelta(
                hours=float(filter_row.interval_hours or 1.0)
            )
            due_ids.append(filter_row.id)

    for filter_id in due_ids:
        celery_app.send_task("tasks.check_whatsapp_messages", args=[filter_id])

    if due_ids:
        logger.info("📅 Dispatched %s due WhatsApp filter scan(s)", len(due_ids))
    return {"scans_dispatched": len(due_ids), "filter_ids": due_ids}


@celery_app.task(bind=True, name="tasks.check_whatsapp_messages", max_retries=2)
def check_whatsapp_messages(self, filter_id: int | None = None) -> dict:
    logger.info("📱 Starting WhatsApp message check for filter=%s...", filter_id)

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_whatsapp_messages_async(filter_id))
        loop.close()
        return result
    except Exception as e:
        logger.error(f"WhatsApp message check failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}


async def _check_whatsapp_messages_async(filter_id: int | None = None) -> dict:
    from services.whatsapp_browser import (
        launch_whatsapp_persistent,
        is_showing_qr,
        wait_for_login,
        navigate_to_whatsapp,
        navigate_to_group,
        scrape_messages_from_current_chat,
        forward_message_to_group,
        safe_close,
    )
    from services.whatsapp_ocr import extract_text_from_image
    from services.whatsapp_matcher import compute_match_score
    from worker.profile_lock import (
        ProfileInUseError,
        acquire_profile_lock,
        release_profile_lock,
    )

    # Load the current session, one filter job, and that job's groups from DB.
    # A NULL filter_id intentionally selects the legacy singleton rows so old
    # manual tasks continue to work while the new scheduler passes an id.
    with get_sync_db() as db:
        from models.whatsapp import (
            WhatsAppSession,
            WhatsAppMonitoredGroup,
            WhatsAppForwardGroup,
            WhatsAppScanFilter,
        )

        session_row = (
            db.query(WhatsAppSession)
            .filter(WhatsAppSession.is_active == True)
            .order_by(WhatsAppSession.id.desc())
            .first()
        )
        if not session_row or session_row.status != "connected":
            logger.warning("⚠️  No active WhatsApp session — skipping check")
            return {"status": "skipped", "reason": "No active session"}

        session_id = session_row.id

        filter_query = db.query(WhatsAppScanFilter)
        if filter_id is not None:
            filters_row = filter_query.filter(WhatsAppScanFilter.id == filter_id).first()
            if not filters_row:
                logger.warning("⚠️  WhatsApp filter %s no longer exists", filter_id)
                return {"status": "skipped", "reason": "Filter not found"}
            if filters_row.status != "active":
                logger.info(
                    "⏸️ WhatsApp filter %s is not active (status=%s)",
                    filter_id,
                    filters_row.status,
                )
                return {"status": "skipped", "reason": "Filter is not active"}
        else:
            # Legacy/manual invocation: prefer an active row but retain the
            # original fallback for databases created before filter jobs.
            filters_row = (
                filter_query.filter(WhatsAppScanFilter.status == "active")
                .order_by(WhatsAppScanFilter.id.desc())
                .first()
                or filter_query.order_by(WhatsAppScanFilter.id.desc()).first()
            )

        group_query = db.query(WhatsAppMonitoredGroup)
        forward_query = db.query(WhatsAppForwardGroup)
        if filter_id is not None:
            group_query = group_query.filter(WhatsAppMonitoredGroup.filter_id == filter_id)
            forward_query = forward_query.filter(WhatsAppForwardGroup.filter_id == filter_id)
        else:
            group_query = group_query.filter(WhatsAppMonitoredGroup.filter_id.is_(None))
            forward_query = forward_query.filter(WhatsAppForwardGroup.filter_id.is_(None))

        monitored_groups_raw = group_query.all()
        logger.info(
            "📋 Loaded %s monitored groups for WhatsApp filter=%s",
            len(monitored_groups_raw),
            filter_id,
        )
        if not monitored_groups_raw:
            logger.warning("⚠️  No monitored groups configured — skipping check")
            return {"status": "skipped", "reason": "No monitored groups"}

        monitored_groups = [
            {
                "id": g.id,
                "group_name": g.group_name,
                "whatsapp_id": g.whatsapp_id,
                "last_message_id": g.last_message_id,
                "last_message_timestamp": g.last_message_timestamp,
            }
            for g in monitored_groups_raw
        ]

        forward_group_row = forward_query.first()
        forward_group = (
            {
                "id": forward_group_row.id,
                "group_name": forward_group_row.group_name,
                "whatsapp_id": forward_group_row.whatsapp_id,
            }
            if forward_group_row
            else None
        )

        if filters_row:
            filter_data = {
                "match_threshold": filters_row.match_threshold,
                "keywords": filters_row.keywords,
                "role": filters_row.role,
                "job_title": filters_row.job_title,
                "experience_level": filters_row.experience_level,
            }
            effective_filter_id = filters_row.id if filter_id is not None else None
        else:
            filter_data = None
            effective_filter_id = None

    if filter_data:
        match_threshold = filter_data["match_threshold"]
        filter_keywords = filter_data["keywords"]
        filter_role = filter_data["role"]
        filter_job_title = filter_data["job_title"]
        filter_experience = filter_data["experience_level"]
    else:
        match_threshold = 60.0
        filter_keywords = None
        filter_role = None
        filter_job_title = None
        filter_experience = None

    logger.info(
        "🔧 Filters: threshold=%s keywords=%s role=%s title=%s exp=%s",
        match_threshold,
        filter_keywords,
        filter_role,
        filter_job_title,
        filter_experience,
    )

    try:
        profile_lock = acquire_profile_lock("whatsapp", blocking_timeout=5)
    except ProfileInUseError:
        logger.info("🔒 WhatsApp profile in use by another browser — skipping this check")
        return {"status": "skipped", "reason": "WhatsApp profile in use"}

    try:
        pw, context, page = await launch_whatsapp_persistent(headless=True)
    except Exception as exc:
        release_profile_lock(profile_lock)
        logger.warning("⚠️  Could not open the WhatsApp profile (%s) — skipping check", exc)
        return {"status": "skipped", "reason": f"Browser unavailable: {exc}"}

    stats = {"scraped": 0, "matched": 0, "rejected": 0, "forwarded": 0, "ocr_failed": 0}

    try:
        await navigate_to_whatsapp(page)

        if not await wait_for_login(page, timeout_seconds=30):
            if await is_showing_qr(page):
                with get_sync_db() as db:
                    from models.whatsapp import WhatsAppSession

                    expired = (
                        db.query(WhatsAppSession)
                        .filter(WhatsAppSession.id == session_id)
                        .first()
                    )
                    if expired:
                        expired.status = "disconnected"
                        expired.is_active = False
                logger.warning("⚠️  WhatsApp session expired (QR screen confirmed)")
                return {"status": "error", "reason": "Session expired"}
            logger.warning("⚠️  WhatsApp Web did not finish loading — skipping check")
            return {"status": "skipped", "reason": "WhatsApp Web slow to load"}

        for group in monitored_groups:
            group_name = group["group_name"]
            logger.info(f"📋 Checking group: {group_name}")

            opened = await navigate_to_group(page, group_name)
            if not opened:
                logger.warning(f"⚠️  Could not open group: {group_name}")
                continue

            new_messages = await scrape_messages_from_current_chat(
                page,
                last_message_id=group.get("last_message_id"),
                last_timestamp=group.get("last_message_timestamp"),
            )

            if not new_messages:
                logger.info(f"📋 No new messages in group: {group_name}")
                with get_sync_db() as db_upd:
                    from models.whatsapp import WhatsAppMonitoredGroup
                    g_row = db_upd.query(WhatsAppMonitoredGroup).filter(
                        WhatsAppMonitoredGroup.id == group["id"]
                    ).first()
                    if g_row:
                        g_row.last_checked_at = datetime.now(timezone.utc)
                continue

            logger.info(f"📋 Found {len(new_messages)} new messages in {group_name}")
            stats["scraped"] += len(new_messages)

            with get_sync_db() as db_save:
                from models.whatsapp import WhatsAppRawMessage, WhatsAppMonitoredGroup

                g_row = db_save.query(WhatsAppMonitoredGroup).filter(
                    WhatsAppMonitoredGroup.id == group["id"]
                ).first()
                if g_row and new_messages:
                    latest_msg = new_messages[0]
                    g_row.last_message_id = latest_msg.get("whatsapp_message_id")
                    g_row.last_message_timestamp = latest_msg.get("timestamp")
                    g_row.last_checked_at = datetime.now(timezone.utc)

                for msg in new_messages:
                    raw_msg = WhatsAppRawMessage(
                        filter_id=effective_filter_id,
                        group_id=group["id"],
                        sender_name=msg.get("sender_name"),
                        message_text=msg.get("message_text"),
                        message_type=msg.get("message_type", "text"),
                        raw_image_bytes=msg.get("raw_image_bytes"),
                        status="pending",
                        whatsapp_message_id=msg.get("whatsapp_message_id"),
                    )
                    db_save.add(raw_msg)

        # ── OCR + Scoring + Forwarding Pass ──────────────────────────────
        with get_sync_db() as db_proc:
            from models.whatsapp import WhatsAppRawMessage, WhatsAppMonitoredGroup

            pending_query = db_proc.query(WhatsAppRawMessage).filter(
                WhatsAppRawMessage.status == "pending"
            )
            if effective_filter_id is not None:
                pending_query = pending_query.filter(
                    WhatsAppRawMessage.filter_id == effective_filter_id
                )
            else:
                pending_query = pending_query.filter(WhatsAppRawMessage.filter_id.is_(None))
            pending_messages = pending_query.all()

            for msg in pending_messages:
                if msg.message_type == "image" and msg.raw_image_bytes:
                    ocr_text, ocr_failed = extract_text_from_image(msg.raw_image_bytes)
                    msg.ocr_text = ocr_text
                    msg.ocr_failed = ocr_failed
                    if ocr_failed:
                        msg.status = "ocr_failed"
                        stats["ocr_failed"] += 1
                        continue

                combined = " ".join(
                    part for part in [msg.message_text or "", msg.ocr_text or ""] if part
                ).strip()

                if not combined:
                    msg.status = "rejected"
                    stats["rejected"] += 1
                    continue

                score = compute_match_score(
                    combined,
                    keywords=filter_keywords,
                    role=filter_role,
                    job_title=filter_job_title,
                    experience_level=filter_experience,
                )
                msg.match_score = score

                if score >= match_threshold:
                    msg.status = "matched"
                    stats["matched"] += 1
                else:
                    msg.status = "rejected"
                    stats["rejected"] += 1

            db_proc.commit()

            if forward_group:
                fwd_name = forward_group["group_name"]
                forward_query_db = db_proc.query(WhatsAppRawMessage).filter(
                    WhatsAppRawMessage.status == "matched",
                    WhatsAppRawMessage.forwarded == False,
                )
                if effective_filter_id is not None:
                    forward_query_db = forward_query_db.filter(
                        WhatsAppRawMessage.filter_id == effective_filter_id
                    )
                else:
                    forward_query_db = forward_query_db.filter(
                        WhatsAppRawMessage.filter_id.is_(None)
                    )
                to_forward = forward_query_db.all()

                for msg in to_forward:
                    combined_text = " ".join(
                        part
                        for part in [msg.message_text or "", msg.ocr_text or ""]
                        if part
                    ).strip()

                    group_obj = (
                        db_proc.query(WhatsAppMonitoredGroup)
                        .filter(WhatsAppMonitoredGroup.id == msg.group_id)
                        .first()
                    )
                    group_name = group_obj.group_name if group_obj else "Unknown"

                    formatted = (
                        f"🔔 Job Match Found\n"
                        f"Score: {msg.match_score}/100\n"
                        f"From: {group_name}\n"
                        f"Sender: {msg.sender_name or 'Unknown'}\n"
                        f"---\n"
                        f"{combined_text[:500]}"
                    )

                    success = await forward_message_to_group(page, fwd_name, formatted)
                    if success:
                        msg.forwarded = True
                        msg.forwarded_at = datetime.now(timezone.utc)
                        stats["forwarded"] += 1
                        logger.info(f"📤 Forwarded message {msg.id} to {fwd_name}")
                    else:
                        logger.error(f"❌ Failed to forward message {msg.id}")

                db_proc.commit()

            # Record the completed scan for this job even when no forward group
            # is configured.  The dispatcher uses next_scan_at, so a paused
            # filter is never scheduled again until the user resumes it.
            from models.whatsapp import WhatsAppScanFilter
            filt_query = db_proc.query(WhatsAppScanFilter)
            if effective_filter_id is not None:
                filt_query = filt_query.filter(WhatsAppScanFilter.id == effective_filter_id)
            else:
                filt_query = filt_query.filter(WhatsAppScanFilter.status == "active")
            filt = filt_query.order_by(WhatsAppScanFilter.id.desc()).first()
            if filt and filt.status == "active":
                scan_time = datetime.now(timezone.utc)
                filt.last_scan_at = scan_time
                filt.next_scan_at = scan_time + timedelta(
                    hours=float(filt.interval_hours or 1.0)
                )
                filt.remaining_seconds = None
            db_proc.commit()

    finally:
        await safe_close(pw, context)
        release_profile_lock(profile_lock)

    logger.info(
        f"📱 WhatsApp check complete: scraped={stats['scraped']} "
        f"matched={stats['matched']} rejected={stats['rejected']} "
        f"forwarded={stats['forwarded']} ocr_failed={stats['ocr_failed']}"
    )
    return stats

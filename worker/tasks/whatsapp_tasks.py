"""
WhatsApp Job Scanner — Celery tasks.
FILE: worker/tasks/whatsapp_tasks.py

Tasks:
  connect_whatsapp     — Launch browser for QR login, save session.
  check_whatsapp_messages — Periodic: scrape groups, OCR images, score, forward.
"""
import asyncio
import json
from datetime import datetime, timezone
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from core.logging_config import get_logger
from worker.celery_app import celery_app

logger = get_logger(__name__)

# ── Sync DB session for Celery ──────────────────────────────────────────────
_sync_url = settings.DATABASE_URL.replace(
    "postgresql+asyncpg://", "postgresql+psycopg2://"
)
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
    """Launch a non-headless Playwright browser for WhatsApp QR login.

    Waits for the user to scan the QR code, then saves the session
    (cookies + storage state) to PostgreSQL.
    """
    logger.info("🚀 Starting WhatsApp connection task...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_connect_whatsapp_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"WhatsApp connect task failed: {e}")
        # Update session status to error
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
    """Async implementation of WhatsApp connection flow."""
    from services.whatsapp_browser import (
        launch_whatsapp_browser,
        navigate_to_whatsapp,
        wait_for_qr_scan,
        get_storage_state,
        safe_close,
    )

    pw, context, page = await launch_whatsapp_browser(headless=False)

    try:
        await navigate_to_whatsapp(page)

        # Update DB status: waiting for QR
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

        # Wait for QR scan (this blocks the Celery task until user scans)
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

        # Extract and save session state
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


# ── Task: Periodic Message Check ─────────────────────────────────────────────


@celery_app.task(bind=True, name="tasks.check_whatsapp_messages", max_retries=2)
def check_whatsapp_messages(self) -> dict:
    """Celery Beat periodic task: check monitored groups for new messages.

    Runs every 2 minutes. Scrapes new messages, runs OCR on images,
    scores against filters, and forwards matches.

    Returns:
        Dict with summary counts.
    """
    logger.info("📱 Starting WhatsApp message check...")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(_check_whatsapp_messages_async())
        loop.close()
        return result
    except Exception as e:
        logger.error(f"WhatsApp message check failed: {e}")
        return {"status": "error", "message": str(e)}


async def _check_whatsapp_messages_async() -> dict:
    """Async implementation of the periodic message check."""
    from services.whatsapp_browser import (
        launch_whatsapp_browser,
        is_logged_in,
        navigate_to_whatsapp,
        navigate_to_group,
        scrape_messages_from_current_chat,
        forward_message_to_group,
        safe_close,
    )
    from services.whatsapp_ocr import extract_text_from_image
    from services.whatsapp_matcher import compute_match_score

    # Load the current session and filters from DB
    with get_sync_db() as db:
        from models.whatsapp import (
            WhatsAppSession,
            WhatsAppMonitoredGroup,
            WhatsAppForwardGroup,
            WhatsAppRawMessage,
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

        storage_state = session_row.storage_state_json

        monitored_groups = db.query(WhatsAppMonitoredGroup).all()
        if not monitored_groups:
            logger.warning("⚠️  No monitored groups configured — skipping check")
            return {"status": "skipped", "reason": "No monitored groups"}

        forward_group = db.query(WhatsAppForwardGroup).first()
        filters = db.query(WhatsAppScanFilter).first()

    if not storage_state:
        logger.error("❌ Session has no stored state — needs re-login")
        return {"status": "error", "reason": "No stored session state"}

    match_threshold = 60.0
    filter_keywords = None
    filter_role = None
    filter_job_title = None
    filter_experience = None

    if filters:
        match_threshold = filters.match_threshold
        filter_keywords = filters.keywords
        filter_role = filters.role
        filter_job_title = filters.job_title
        filter_experience = filters.experience_level

    pw, context, page = await launch_whatsapp_browser(
        headless=True, storage_state=storage_state
    )

    stats = {"scraped": 0, "matched": 0, "rejected": 0, "forwarded": 0, "ocr_failed": 0}

    try:
        await navigate_to_whatsapp(page)

        if not await is_logged_in(page):
            # Session expired — update DB
            with get_sync_db() as db:
                from models.whatsapp import WhatsAppSession

                expired = (
                    db.query(WhatsAppSession)
                    .filter(WhatsAppSession.id == session_row.id)
                    .first()
                )
                if expired:
                    expired.status = "disconnected"
                    expired.is_active = False
            logger.warning("⚠️  WhatsApp session expired")
            return {"status": "error", "reason": "Session expired"}

        # Process each monitored group
        for group in monitored_groups:
            logger.info(f"📋 Checking group: {group.group_name}")

            opened = await navigate_to_group(page, group.group_name)
            if not opened:
                logger.warning(f"⚠️  Could not open group: {group.group_name}")
                continue

            # Scrape new messages since last check
            new_messages = await scrape_messages_from_current_chat(
                page,
                last_message_id=group.last_message_id,
                last_timestamp=group.last_message_timestamp,
            )

            if not new_messages:
                logger.info(f"📋 No new messages in group: {group.group_name}")
                continue

            logger.info(
                f"📋 Found {len(new_messages)} new messages in {group.group_name}"
            )
            stats["scraped"] += len(new_messages)

            # Update last_message_id for the group
            if new_messages:
                latest_msg = new_messages[0]  # Most recent (top of chat)
                group.last_message_id = latest_msg.get("whatsapp_message_id")
                group.last_message_timestamp = latest_msg.get("timestamp")
                group.last_checked_at = datetime.now(timezone.utc)

            # Save raw messages to DB
            with get_sync_db() as db_save:
                from models.whatsapp import WhatsAppRawMessage

                for msg in new_messages:
                    raw_msg = WhatsAppRawMessage(
                        group_id=group.id,
                        sender_name=msg.get("sender_name"),
                        message_text=msg.get("message_text"),
                        message_type=msg.get("message_type", "text"),
                        raw_image_bytes=msg.get("raw_image_bytes"),
                        status="pending",
                        whatsapp_message_id=msg.get("whatsapp_message_id"),
                    )
                    db_save.add(raw_msg)
                db_save.commit()

        # ── OCR + Scoring + Forwarding Pass ──────────────────────────────
        with get_sync_db() as db_proc:
            from models.whatsapp import WhatsAppRawMessage

            pending_messages = (
                db_proc.query(WhatsAppRawMessage)
                .filter(WhatsAppRawMessage.status == "pending")
                .all()
            )

            for msg in pending_messages:
                # ── Step 1: OCR for image messages ──
                if msg.message_type == "image" and msg.raw_image_bytes:
                    ocr_text, ocr_failed = extract_text_from_image(msg.raw_image_bytes)
                    msg.ocr_text = ocr_text
                    msg.ocr_failed = ocr_failed
                    if ocr_failed:
                        msg.status = "ocr_failed"
                        stats["ocr_failed"] += 1
                        continue

                # ── Step 2: Combine text ──
                combined = " ".join(
                    part for part in [msg.message_text or "", msg.ocr_text or ""] if part
                ).strip()

                if not combined:
                    msg.status = "rejected"
                    stats["rejected"] += 1
                    continue

                # ── Step 3: Score ──
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

            # ── Step 4: Forward matched messages ──
            if forward_group:
                to_forward = (
                    db_proc.query(WhatsAppRawMessage)
                    .filter(
                        WhatsAppRawMessage.status == "matched",
                        WhatsAppRawMessage.forwarded == False,
                    )
                    .all()
                )

                for msg in to_forward:
                    combined_text = " ".join(
                        part
                        for part in [msg.message_text or "", msg.ocr_text or ""]
                        if part
                    ).strip()

                    # Get group name
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

                    success = await forward_message_to_group(
                        page, forward_group.group_name, formatted
                    )
                    if success:
                        msg.forwarded = True
                        msg.forwarded_at = datetime.now(timezone.utc)
                        stats["forwarded"] += 1
                        logger.info(
                            f"📤 Forwarded message {msg.id} to {forward_group.group_name}"
                        )
                    else:
                        logger.error(f"❌ Failed to forward message {msg.id}")

                db_proc.commit()

    finally:
        await safe_close(pw, context)

    logger.info(
        f"📱 WhatsApp check complete: scraped={stats['scraped']} "
        f"matched={stats['matched']} rejected={stats['rejected']} "
        f"forwarded={stats['forwarded']} ocr_failed={stats['ocr_failed']}"
    )
    return stats

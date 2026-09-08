"""Assistant context — the "current user data" half of the system prompt.

FILE: services/ai/assistant/context.py

Builds a compact, read-only snapshot of the caller's own state: which
channels are connected (Meta socials, Gmail, WhatsApp, LinkedIn), whether a
WhatsApp live-browser session is running, and counts of their jobs/posts.
It answers "what should the assistant know about me before replying?" in a
handful of cheap indexed queries — no message bodies, no tokens, no other
user's rows (everything filters on ``owner_email == current_user.email``).

The snapshot is injected into the system prompt so the model can say "you
haven't connected Instagram yet — want me to take you there?" without a tool
round-trip. Deep or live data (unread counts, chat previews) stays behind
tools, which the model calls only when the user actually asks.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.campaign import Campaign
from models.feed_scroll_job import FeedScrollJob
from models.gmail import GmailConnection
from models.linkedin_account import LinkedInAccount
from models.social_scheduler import SocialPlatformConnection, SocialPost
from models.user import User
from models.whatsapp import WhatsAppScanFilter, WhatsAppSession

logger = logging.getLogger(__name__)


async def _count(db: AsyncSession, query) -> int:
    return int((await db.execute(query)).scalar() or 0)


async def _whatsapp_live_status(user: User) -> dict[str, Any]:
    """Is one of the caller's WhatsApp sessions driving a live browser?

    Imported lazily: the browser manager module pulls in Playwright-adjacent
    machinery and the context snapshot should stay import-cheap for tests.
    """
    try:
        from services.whatsapp_live_browser import all_live_browsers
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("assistant context: live browser registry unavailable: %s", exc)
        return {"connected_sessions": 0, "live_running": False}

    running = False
    for manager in all_live_browsers():
        if manager.status == "running":
            running = True
            break
    return {"live_running": running}


async def build_user_snapshot(db: AsyncSession, user: User) -> dict[str, Any]:
    """Everything the system prompt needs to know about the caller."""
    owner = user.email

    social_rows = (
        await db.execute(
            select(
                SocialPlatformConnection.platform,
                func.count(SocialPlatformConnection.id),
            )
            .where(SocialPlatformConnection.owner_email == owner)
            .group_by(SocialPlatformConnection.platform)
        )
    ).all()
    socials = {platform: count for platform, count in social_rows}

    gmail_rows = (
        await db.execute(
            select(GmailConnection.account_email).where(GmailConnection.owner_email == owner)
        )
    ).scalars().all()

    whatsapp_sessions = await _count(
        db,
        select(func.count(WhatsAppSession.id)).where(
            WhatsAppSession.owner_email == owner, WhatsAppSession.is_active.is_(True)
        ),
    )
    live = await _whatsapp_live_status(user)

    linkedin_accounts = await _count(
        db,
        select(func.count(LinkedInAccount.id)).where(LinkedInAccount.owner_email == owner),
    )

    # Campaigns and leads hang off the LinkedIn account's email, not the
    # LinkEasy owner's email, so the owner's campaigns are "campaigns whose
    # account belongs to me".
    linkedin_emails = (
        await db.execute(
            select(LinkedInAccount.linkedin_email).where(LinkedInAccount.owner_email == owner)
        )
    ).scalars().all()
    campaigns = (
        await _count(
            db, select(func.count(Campaign.id)).where(Campaign.account_email.in_(linkedin_emails))
        )
        if linkedin_emails
        else 0
    )

    snapshot: dict[str, Any] = {
        "connected_channels": {
            # Channel → connected account count (0 = not connected).
            "instagram": socials.get("instagram", 0),
            "messenger": socials.get("facebook", 0),
            "gmail": len(gmail_rows),
            "whatsapp": whatsapp_sessions,
            "linkedin": linkedin_accounts,
            "socials": socials,  # publishing platforms: youtube/facebook/instagram/tiktok
        },
        "gmail_mailboxes": list(gmail_rows),
        "whatsapp_live_browser_running": live["live_running"],
        "counts": {
            "campaigns": campaigns,
            "feed_scan_jobs": await _count(
                db,
                select(func.count(FeedScrollJob.id)).where(FeedScrollJob.owner_email == owner),
            ),
            "scheduled_posts": await _count(
                db,
                select(func.count(SocialPost.id)).where(SocialPost.owner_email == owner),
            ),
            "whatsapp_group_filters": await _count(
                db,
                select(func.count(WhatsAppScanFilter.id)).where(
                    WhatsAppScanFilter.owner_email == owner
                ),
            ),
        },
    }
    return snapshot


def render_snapshot(snapshot: dict[str, Any]) -> str:
    """Snapshot → the compact JSON-ish block for the system prompt."""
    channels = snapshot.get("connected_channels", {})
    counts = snapshot.get("counts", {})
    lines = [
        f"Instagram accounts: {channels.get('instagram', 0)}",
        f"Messenger/Facebook accounts: {channels.get('messenger', 0)}",
        f"Gmail mailboxes: {channels.get('gmail', 0)}",
        f"WhatsApp sessions: {channels.get('whatsapp', 0)}"
        + (
            " (live browser RUNNING)"
            if snapshot.get("whatsapp_live_browser_running")
            else " (live browser not running)"
        ),
        f"LinkedIn accounts: {channels.get('linkedin', 0)}",
        f"Publishing connections: {channels.get('socials')}",
        "Work: "
        f"{counts.get('campaigns', 0)} campaigns, "
        f"{counts.get('feed_scan_jobs', 0)} feed-scan jobs, "
        f"{counts.get('scheduled_posts', 0)} scheduled posts, "
        f"{counts.get('whatsapp_group_filters', 0)} WhatsApp group filters",
    ]
    return "\n".join(lines)


__all__ = ["build_user_snapshot", "render_snapshot"]

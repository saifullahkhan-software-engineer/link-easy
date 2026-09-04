"""
Public account-deletion endpoints — Meta's "User Data Deletion" requirement.

Meta app review requires a User Data Deletion callback URL: a page where a
user can ask to delete their data. The frontend hosts that page
(``/delete``) and the whole flow is email-confirmed and generic:

  POST /deletion-request   {email}
      → if the account exists, emails a ONE-TIME signed confirmation link
        (DATA_DELETION_URL?token=…). The response is identical whether or
        not the account exists — no account enumeration, ever.
  POST /deletion-confirm   {token}
      → validates the signed one-time token, deletes the user and every row
        they own (FK-safe order), clears their outstanding one-time tokens
        (password resets + deletion links) and returns a generic message.

Both endpoints are anonymous and rate-limited (``user-data:request`` /
``user-data:confirm`` buckets) so a guessed token cannot be brute-forced and
a stranger cannot spam someone's inbox. Deletion NEVER happens from a bare
email address — only from a link the account's owner received by email.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import delete as sa_delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.rate_limit_deps import rate_limit
from core.config import settings
from core.email import EmailDeliveryError, send_account_deletion_email
from core.security import create_account_deletion_token, generate_token_id
from models.campaign import Campaign, CampaignStep
from models.campaign_job import CampaignJob
from models.feed_lead import FeedLead
from models.feed_scroll_applied_post import FeedScrollAppliedPost
from models.feed_scroll_job import FeedScrollJob
from models.feed_scroll_result import FeedScrollResult
from models.lead import Lead
from models.linkedin_account import LinkedInAccount
from models.rbac import UserRoleLink
from models.social_scheduler import (
    SocialPlatformConnection,
    SocialPost,
    SocialPostResult,
)
from models.user import PasswordResetToken, User, UserDeletionToken
from models.whatsapp import (
    WhatsAppForwardGroup,
    WhatsAppMonitoredGroup,
    WhatsAppRawMessage,
    WhatsAppScanFilter,
    WhatsAppSession,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/user-data", tags=["user-data"])

# Identical copy whether or not the account exists (and whether or not the
# email could be delivered) — the endpoints must never reveal account
# existence. That is what keeps this generic for Meta's review AND safe.
DELETION_REQUEST_MESSAGE = (
    "If an account exists for this email, a confirmation link with "
    "instructions is on its way. Check your inbox (and spam folder)."
)
DELETION_INVALID_TOKEN_MESSAGE = (
    "This deletion link is invalid or has expired. Request a new one from "
    "the account deletion page."
)
DELETION_COMPLETE_MESSAGE = "Your account and all associated data have been deleted."

# The signed token and its DB row share this window (same idea as password
# resets), so a stale confirmation link cannot be used weeks later.
ACCOUNT_DELETION_TOKEN_MINUTES = 30


class AccountDeletionRequest(BaseModel):
    email: EmailStr


class DeletionConfirmationRequest(BaseModel):
    token: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _normalise_email(email: str) -> str:
    return (email or "").strip().lower()


async def _delete_user_and_data(db: AsyncSession, email: str) -> None:
    """Delete a user and every row they own, in FK-safe order.

    The newer model tables declare ownership FKs with ``ondelete=CASCADE``,
    but several original base tables (campaigns, leads, jobs, feed tables)
    were created without DB-level cascades, and SQLite (the test suite) only
    enforces FKs when a PRAGMA is on. Explicitly deleting every owned row in
    dependency order keeps the wipe correct on Postgres, SQLite and any
    future engine. The caller commits once so the whole wipe is atomic.
    """
    # ── Owned LinkedIn accounts + the campaigns/feed jobs under them ──────────
    linkedin_emails = list(
        (
            await db.execute(
                select(LinkedInAccount.linkedin_email).where(
                    LinkedInAccount.owner_email == email
                )
            )
        )
        .scalars()
        .all()
    )

    campaign_ids: list[str] = []
    if linkedin_emails:
        campaign_ids = list(
            (
                await db.execute(
                    select(Campaign.id).where(
                        Campaign.account_email.in_(linkedin_emails)
                    )
                )
            )
            .scalars()
            .all()
        )

    feed_job_ids: list[str] = []
    if linkedin_emails:
        feed_job_ids = list(
            (
                await db.execute(
                    select(FeedScrollJob.id).where(
                        or_(
                            FeedScrollJob.owner_email == email,
                            FeedScrollJob.account_email.in_(linkedin_emails),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
    # Feed jobs the user owns directly (edge case: a job against an account
    # that is not theirs) also belong to them.
    owned_feed_job_ids = list(
        (
            await db.execute(
                select(FeedScrollJob.id).where(
                    FeedScrollJob.owner_email == email
                )
            )
        )
        .scalars()
        .all()
    )
    seen_feed_ids = set(feed_job_ids)
    for job_id in owned_feed_job_ids:
        if job_id not in seen_feed_ids:
            seen_feed_ids.add(job_id)
            feed_job_ids.append(job_id)

    # Campaign children first (their FKs point at campaigns / leads), then
    # the campaigns themselves, then the linkedin accounts they referenced.
    if campaign_ids:
        await db.execute(
            sa_delete(CampaignJob).where(CampaignJob.campaign_id.in_(campaign_ids))
        )
        await db.execute(
            sa_delete(CampaignStep).where(CampaignStep.campaign_id.in_(campaign_ids))
        )
        await db.execute(sa_delete(Lead).where(Lead.campaign_id.in_(campaign_ids)))
        await db.execute(sa_delete(Campaign).where(Campaign.id.in_(campaign_ids)))

    # Feed-scroll children, then the jobs, then the linkedin accounts.
    if feed_job_ids:
        await db.execute(
            sa_delete(FeedScrollResult).where(
                FeedScrollResult.feed_scroll_job_id.in_(feed_job_ids)
            )
        )
        await db.execute(
            sa_delete(FeedLead).where(
                or_(
                    FeedLead.feed_scroll_job_id.in_(feed_job_ids),
                    FeedLead.owner_email == email,
                )
            )
        )
        await db.execute(
            sa_delete(FeedScrollAppliedPost).where(
                or_(
                    FeedScrollAppliedPost.feed_scroll_job_id.in_(feed_job_ids),
                    FeedScrollAppliedPost.owner_email == email,
                )
            )
        )
        await db.execute(
            sa_delete(FeedScrollJob).where(FeedScrollJob.id.in_(feed_job_ids))
        )
    else:
        # Owner-scoped cleanup for rows whose job id lookup found nothing.
        await db.execute(sa_delete(FeedLead).where(FeedLead.owner_email == email))
        await db.execute(
            sa_delete(FeedScrollAppliedPost).where(
                FeedScrollAppliedPost.owner_email == email
            )
        )

    if linkedin_emails:
        await db.execute(
            sa_delete(LinkedInAccount).where(
                LinkedInAccount.owner_email == email
            )
        )

    # ── WhatsApp scanner rows (session + filter trees) ────────────────────────
    filter_ids = list(
        (
            await db.execute(
                select(WhatsAppScanFilter.id).where(
                    WhatsAppScanFilter.owner_email == email
                )
            )
        )
        .scalars()
        .all()
    )
    if filter_ids:
        await db.execute(
            sa_delete(WhatsAppRawMessage).where(
                WhatsAppRawMessage.filter_id.in_(filter_ids)
            )
        )
        await db.execute(
            sa_delete(WhatsAppMonitoredGroup).where(
                WhatsAppMonitoredGroup.filter_id.in_(filter_ids)
            )
        )
        await db.execute(
            sa_delete(WhatsAppForwardGroup).where(
                WhatsAppForwardGroup.filter_id.in_(filter_ids)
            )
        )
        await db.execute(
            sa_delete(WhatsAppScanFilter).where(
                WhatsAppScanFilter.id.in_(filter_ids)
            )
        )
    await db.execute(
        sa_delete(WhatsAppSession).where(WhatsAppSession.owner_email == email)
    )

    # ── Social scheduler (posts, per-platform results, connections) ───────────
    # Collect the video files first so they can be removed after the commit
    # (best-effort — the DB wipe is what matters, files must not block it).
    video_paths = list(
        (
            await db.execute(
                select(SocialPost.video_path).where(SocialPost.owner_email == email)
            )
        )
        .scalars()
        .all()
    )
    await db.execute(
        sa_delete(SocialPostResult).where(SocialPostResult.owner_email == email)
    )
    await db.execute(sa_delete(SocialPost).where(SocialPost.owner_email == email))
    await db.execute(
        sa_delete(SocialPlatformConnection).where(
            SocialPlatformConnection.owner_email == email
        )
    )

    # ── Roles + one-time tokens, then the user row itself ─────────────────────
    await db.execute(sa_delete(UserRoleLink).where(UserRoleLink.user_email == email))
    await db.execute(
        sa_delete(PasswordResetToken).where(PasswordResetToken.email == email)
    )
    await db.execute(
        sa_delete(UserDeletionToken).where(UserDeletionToken.email == email)
    )
    await db.execute(sa_delete(User).where(User.email == email))

    if video_paths:
        try:
            for path in video_paths:
                if path and os.path.isfile(path):
                    os.remove(path)
        except OSError as exc:  # best-effort cleanup
            logger.warning("Could not remove social upload file(s) for %s: %s", email, exc)


@router.post(
    "/deletion-request",
    dependencies=[Depends(rate_limit("user-data:request"))],
)
async def request_account_deletion(
    data: AccountDeletionRequest,
    db: AsyncSession = Depends(get_db),
):
    email = _normalise_email(data.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()

    if user is None:
        # Same generic message as the success path — never reveal whether an
        # account exists under this email.
        return {"message": DELETION_REQUEST_MESSAGE}

    now = _utc_now()
    token_id = generate_token_id()
    token = create_account_deletion_token(user.email, token_id)
    db.add(
        UserDeletionToken(
            token_id=token_id,
            email=user.email,
            expires_at=now + timedelta(minutes=ACCOUNT_DELETION_TOKEN_MINUTES),
        )
    )
    await db.commit()

    reset_link = f"{settings.DATA_DELETION_URL}?token={token}"
    try:
        await send_account_deletion_email(user.email, reset_link)
    except EmailDeliveryError:
        # The email infrastructure is down — log it but keep the response
        # generic so this endpoint cannot be used to probe for accounts.
        logger.warning("Account-deletion email to %s could not be sent", user.email)

    return {"message": DELETION_REQUEST_MESSAGE}


@router.post(
    "/deletion-confirm",
    dependencies=[Depends(rate_limit("user-data:confirm"))],
)
async def confirm_account_deletion(
    data: DeletionConfirmationRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        payload = jwt.decode(
            data.token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        if payload.get("token_type") != "account_deletion":
            raise JWTError("Wrong token type")
        email = payload.get("sub")
        token_id = payload.get("jti")
        if not email or not token_id:
            raise JWTError("Missing subject")
    except JWTError:
        raise HTTPException(status_code=400, detail=DELETION_INVALID_TOKEN_MESSAGE)

    result = await db.execute(
        select(UserDeletionToken).where(UserDeletionToken.token_id == token_id)
    )
    token_row = result.scalars().first()
    if (
        token_row is None
        or _normalise_email(token_row.email) != _normalise_email(email)
        or _ensure_aware(token_row.expires_at) <= _utc_now()
    ):
        raise HTTPException(status_code=400, detail=DELETION_INVALID_TOKEN_MESSAGE)

    email = _normalise_email(email)

    # The link is valid and unused. Delete everything the account owns,
    # consume the one-time token rows, and commit as one atomic transaction.
    await _delete_user_and_data(db, email)
    await db.commit()
    logger.info("Account deletion completed for %s", email)
    return {"message": DELETION_COMPLETE_MESSAGE}

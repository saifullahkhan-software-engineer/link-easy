"""Ultimate Inbox: read and reply using the caller's existing Meta connections.

Multi-account: a user can connect several Meta accounts (two Facebook Pages,
a personal and a work Instagram). Every route takes an optional ``account_id``
query parameter — the id of one ``social_platform_connections`` row — and
resolves it within the caller's own rows for that channel. Without it the
first-connected account is used, so single-account users and older clients
work unchanged. ``GET /accounts`` feeds the channel's account dropdown.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from models.social_scheduler import SocialPlatformConnection
from models.user import User
from schemas.inbox import (
    InboxAccount,
    InboxAccountsResponse,
    InboxConversationsResponse,
    InboxMessagesResponse,
    InboxReplyRequest,
    InboxReplyResponse,
)
from services.social.connections import read_tokens, reconnect_required
from services.social.inbox import InboxError, MetaInboxService

router = APIRouter(prefix="/api/v1/inbox", tags=["ultimate-inbox"])
PLATFORMS = {"instagram": "instagram", "messenger": "facebook"}


async def inbox_service(
    channel: str,
    response: Response,
    account_id: str | None = Query(
        default=None,
        description="One of the caller's connected accounts for this channel; omit for the first-connected",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    platform = PLATFORMS.get(channel)
    if not platform:
        # WhatsApp has its existing API. Business is intentionally not enabled.
        raise HTTPException(status_code=404, detail="Inbox channel not found")
    query = (
        select(SocialPlatformConnection)
        .where(
            SocialPlatformConnection.owner_email == current_user.email,
            SocialPlatformConnection.platform == platform,
        )
        .order_by(
            SocialPlatformConnection.created_at.asc(),
            SocialPlatformConnection.id.asc(),
        )
    )
    if account_id:
        # Resolve the pick inside the caller's own rows: a foreign or
        # stale id is not found, never used.
        query = query.where(SocialPlatformConnection.id == account_id)
    connection = (await db.execute(query)).scalars().first()
    if connection is None:
        if account_id:
            raise HTTPException(
                status_code=404,
                detail="That account is no longer connected to this inbox — pick another one.",
            )
        raise HTTPException(status_code=409, detail=f"Connect {platform.title()} in Accounts → Socials to use this inbox.")
    try:
        tokens = read_tokens(connection)
        if tokens.is_expired:
            raise HTTPException(status_code=409, detail="Meta access has expired. Reconnect in Accounts → Socials.")
        return MetaInboxService(
            channel, connection.account_id, tokens.access_token,
            page_id=(connection.extra_data or {}).get("page_id", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="This connection needs to be renewed in Accounts → Socials.") from exc
    except InboxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/accounts", response_model=InboxAccountsResponse)
async def list_inbox_accounts(
    channel: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's connected accounts for one inbox channel.

    Feeds the channel's account dropdown: with one account the UI shows the
    name, with several it offers the pick (oldest first, the default choice).
    """
    platform = PLATFORMS.get(channel)
    if not platform:
        raise HTTPException(status_code=404, detail="Inbox channel not found")
    result = await db.execute(
        select(SocialPlatformConnection)
        .where(
            SocialPlatformConnection.owner_email == current_user.email,
            SocialPlatformConnection.platform == platform,
        )
        .order_by(
            SocialPlatformConnection.created_at.asc(),
            SocialPlatformConnection.id.asc(),
        )
    )
    rows = result.scalars().all()
    return InboxAccountsResponse(
        platform=platform,
        accounts=[
            InboxAccount(
                id=row.id,
                account_id=row.account_id or "",
                account_name=row.account_name or "",
                reconnect_required=reconnect_required(row),
                connected_at=row.created_at,
            )
            for row in rows
        ],
    )


async def _result(action):
    try:
        return await action
    except InboxError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/{channel}/conversations", response_model=InboxConversationsResponse,
            dependencies=[Depends(rate_limit("inbox:read"))])
async def list_conversations(
    after: str | None = Query(default=None, max_length=4096),
    service: MetaInboxService = Depends(inbox_service),
):
    return await _result(service.list_conversations(after))


@router.get("/{channel}/messages", response_model=InboxMessagesResponse,
            dependencies=[Depends(rate_limit("inbox:read"))])
async def list_messages(
    conversation_id: str = Query(min_length=1, max_length=2048),
    service: MetaInboxService = Depends(inbox_service),
):
    return await _result(service.list_messages(conversation_id))


@router.post("/{channel}/messages", response_model=InboxReplyResponse,
             dependencies=[Depends(rate_limit("inbox:send"))])
async def send_reply(
    payload: InboxReplyRequest,
    service: MetaInboxService = Depends(inbox_service),
):
    return await _result(service.send_reply(payload.conversation_id, payload.text))

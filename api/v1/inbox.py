"""Ultimate Inbox: read and reply using the caller's existing Meta connections."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from models.social_scheduler import SocialPlatformConnection
from models.user import User
from schemas.inbox import (
    InboxConversationsResponse, InboxMessagesResponse, InboxReplyRequest, InboxReplyResponse,
)
from services.social.connections import read_tokens
from services.social.inbox import InboxError, MetaInboxService

router = APIRouter(prefix="/api/v1/inbox", tags=["ultimate-inbox"])
PLATFORMS = {"instagram": "instagram", "messenger": "facebook"}


async def inbox_service(
    channel: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    platform = PLATFORMS.get(channel)
    if not platform:
        # WhatsApp has its existing API. Business is intentionally not enabled.
        raise HTTPException(status_code=404, detail="Inbox channel not found")
    connection = (
        await db.execute(select(SocialPlatformConnection).where(
            SocialPlatformConnection.owner_email == current_user.email,
            SocialPlatformConnection.platform == platform,
        ))
    ).scalar_one_or_none()
    if connection is None:
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

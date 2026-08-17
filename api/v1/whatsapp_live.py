"""
WhatsApp live chat — REST API.

FILE: api/v1/whatsapp_live.py

POST   /api/v1/whatsapp/live/start             → launch the live-chat browser
POST   /api/v1/whatsapp/live/stop              → close the live-chat browser
GET    /api/v1/whatsapp/live/status            → snapshot of the live manager
GET    /api/v1/whatsapp/live/chats            → list chats (q= filter)
POST   /api/v1/whatsapp/live/chats/open        → open a chat by id
POST   /api/v1/whatsapp/live/chats/close       → leave the active chat
GET    /api/v1/whatsapp/live/messages          → read the active chat's messages
POST   /api/v1/whatsapp/live/messages/send     → send a manual message

The manager singleton lives in services.whatsapp_live_browser.py. It holds
the shared ``profile_lock:whatsapp`` Redis key, so while live chat is active
the periodic Celery scan task pauses with ProfileInUseError instead of
fighting the browser and disconnecting the user.

Each /send call is paced by ``WHATSAPP_FORWARD_DELAY_SECONDS`` (10s default)
so a rapid-typing user does not trip WhatsApp's spam/blocking filter. The
response surfaces ``throttled_seconds`` so the UI can show the wait.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from api.dependencies import get_current_user, get_db
from core.logging_config import get_logger
from models.user import User
from models.whatsapp import WhatsAppSession
from schemas.whatsapp_live import (
    LiveChatItem,
    LiveChatListResponse,
    LiveMessageItem,
    LiveMessagesResponse,
    LiveOpenChatRequest,
    LiveOpenChatResponse,
    LiveSendRequest,
    LiveSendResponse,
    LiveStartResponse,
)
from services.whatsapp_live_browser import (
    DEFAULT_CHAT_LIMIT,
    DEFAULT_MESSAGE_LIMIT,
    live_browser,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/whatsapp/live", tags=["whatsapp-live"])


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _require_connection(db: AsyncSession) -> None:
    """Block the live browser from starting when WhatsApp isn't connected."""
    session_row = (
        (
            await db.execute(
                select(WhatsAppSession)
                .filter(WhatsAppSession.is_active == True)
                .order_by(WhatsAppSession.id.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if not session_row or session_row.status != "connected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "WhatsApp is not connected. Connect via the WhatsApp Scanner "
                "page before opening live chat."
            ),
        )


def _snapshot_response() -> LiveStartResponse:
    snap = live_browser.snapshot()
    return LiveStartResponse(**snap)


# ── Lifecycle ────────────────────────────────────────────────────────────────


@router.post("/start", response_model=LiveStartResponse)
async def start_live_chat(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveStartResponse:
    await _require_connection(db)

    # Run on the API event loop. launch_whatsapp_persistent + is_logged_in
    # takes a few seconds; not an issue for an explicit user action.
    result = await live_browser.start()
    resp = LiveStartResponse(**result)
    if resp.status == "error":
        # Surface a 503 for a clean client-side error message.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=resp)
    return resp


@router.post("/stop", response_model=LiveStartResponse)
async def stop_live_chat(
    _current_user: User = Depends(get_current_user),
) -> LiveStartResponse:
    result = await live_browser.stop()
    return LiveStartResponse(**result)


@router.get("/status", response_model=LiveStartResponse)
async def live_status(
    _current_user: User = Depends(get_current_user),
) -> LiveStartResponse:
    return _snapshot_response()


# ── Chat list ────────────────────────────────────────────────────────────────


@router.get("/chats", response_model=LiveChatListResponse)
async def list_live_chats(
    q: Optional[str] = Query(None, description="Filter chats via WhatsApp search"),
    limit: int = Query(DEFAULT_CHAT_LIMIT, ge=1, le=200),
    _current_user: User = Depends(get_current_user),
) -> LiveChatListResponse:
    if live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running. Call POST /live/start first.",
        )

    chats = await live_browser.list_chats(filter_text=q, limit=limit)
    items = [LiveChatItem(**c) for c in chats]
    return LiveChatListResponse(
        chats=items,
        count=len(items),
        query=q,
    )


# ── Active chat ──────────────────────────────────────────────────────────────


@router.post("/chats/open", response_model=LiveOpenChatResponse)
async def open_live_chat(
    payload: LiveOpenChatRequest,
    _current_user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    if live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running.",
        )
    result = await live_browser.open_chat(payload.chat_id)
    if not result.get("ok"):
        return LiveOpenChatResponse(ok=False, error=result.get("error"))
    return LiveOpenChatResponse(
        ok=True,
        chat_id=result.get("chat_id"),
        name=result.get("name"),
    )


@router.post("/chats/close", response_model=LiveOpenChatResponse)
async def close_live_chat(
    _current_user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    result = await live_browser.close_active_chat()
    return LiveOpenChatResponse(**result)


# ── Reading / writing ────────────────────────────────────────────────────────


@router.get("/messages", response_model=LiveMessagesResponse)
async def get_live_messages(
    limit: int = Query(DEFAULT_MESSAGE_LIMIT, ge=1, le=200),
    _current_user: User = Depends(get_current_user),
) -> LiveMessagesResponse:
    if live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running.",
        )
    if not live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No chat is currently open. POST /live/chats/open first."
            ),
        )

    messages = await live_browser.read_messages(limit=limit)
    items = [LiveMessageItem(**m) for m in messages]
    return LiveMessagesResponse(
        chat_id=live_browser.active_chat_id,
        chat_name=live_browser.active_chat_name,
        messages=items,
        count=len(items),
    )


@router.post("/messages/send", response_model=LiveSendResponse)
async def send_live_message(
    payload: LiveSendRequest,
    _current_user: User = Depends(get_current_user),
) -> LiveSendResponse:
    if live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running.",
        )
    if not live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open a chat before sending.",
        )

    # send_message itself inserts the throttle sleep before the click; we
    # don't need to add another one here. Run synchronously so the response
    # reflects the actual send outcome (browser errors) and the cooldown
    # duration, both of which the frontend can render.
    t0 = asyncio.get_running_loop().time()
    result = await live_browser.send_message(payload.text)
    elapsed = asyncio.get_running_loop().time() - t0

    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Send failed",
        )

    return LiveSendResponse(
        ok=True,
        # The browser always includes 0.3–1s of internal paste/click lag so
        # the "throttled" snapshot under-reports the actual wait. Cap it to
        # keep the message friendly: "Sent (waited ~3.2s for the previous one)".
        throttled_seconds=round(elapsed, 2),
    )

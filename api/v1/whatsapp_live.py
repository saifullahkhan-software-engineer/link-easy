"""
WhatsApp live chat — REST API (per-user sessions).

FILE: api/v1/whatsapp_live.py

POST   /api/v1/whatsapp/live/start             → launch the caller's live-chat browser
POST   /api/v1/whatsapp/live/stop              → close the caller's live-chat browser
GET    /api/v1/whatsapp/live/status            → snapshot of the caller's live manager
GET    /api/v1/whatsapp/live/chats            → list chats (q= filter)
POST   /api/v1/whatsapp/live/chats/open        → open a chat by id
POST   /api/v1/whatsapp/live/chats/close       → leave the active chat
GET    /api/v1/whatsapp/live/messages          → read the active chat's messages
POST   /api/v1/whatsapp/live/messages/send     → send a manual message

Per-user rollout: every WhatsApp session owns a LiveBrowserManager
(``services.whatsapp_live_browser.get_live_browser(session_id)``), so ten
users can run live chat on ten different WhatsApp numbers simultaneously.
Each manager holds its own session's ``profile_lock:whatsapp:{id}``, so while
one user's live chat is active only that user's scan task pauses with
ProfileInUseError — every other user is unaffected.

Each /send call is paced by ``WHATSAPP_FORWARD_DELAY_SECONDS`` (10s default)
so a rapid-typing user does not trip WhatsApp's spam/blocking filter. The
response surfaces ``throttled_seconds`` so the UI can show the wait.
"""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from api.v1.whatsapp_sessions import get_owned_session
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
    get_live_browser,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/whatsapp/live", tags=["whatsapp-live"])


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _require_connection(
    db: AsyncSession, current_user: User
) -> WhatsAppSession:
    """Resolve the caller's connected session or raise a readable 400."""
    return await get_owned_session(db, current_user, require_connected=True)


def _manager_for(session: Optional[WhatsAppSession]):
    """The live-chat manager that owns ``session`` (legacy when None)."""
    return get_live_browser(getattr(session, "id", None) if session else None)


async def _require_running(db: AsyncSession, current_user: User):
    """Return the caller's running manager or raise the uniform 409."""
    session = await get_owned_session(db, current_user)
    manager = _manager_for(session)
    if manager.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running. Call POST /live/start first.",
        )
    return manager


def _snapshot_response(manager) -> LiveStartResponse:
    return LiveStartResponse(**manager.snapshot())


# ── Lifecycle ────────────────────────────────────────────────────────────────


@router.post(
    "/start",
    response_model=LiveStartResponse,
    dependencies=[Depends(rate_limit("live:start"))],
)
async def start_live_chat(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveStartResponse:
    session = await _require_connection(db, current_user)
    manager = _manager_for(session)

    # Run on the API event loop. Browser launch + is_logged_in takes a few
    # seconds; not an issue for an explicit user action.
    result = await manager.start()
    resp = LiveStartResponse(**result)
    if resp.status == "error":
        # Surface a 503 with a *string* detail. Passing the Pydantic model
        # itself made Starlette's JSONResponse raise "Object of type
        # LiveStartResponse is not JSON serializable", so every failed start
        # turned into an opaque 500 "Internal Server Error" and the real
        # reason (not connected / profile busy / session expired) never
        # reached the user. getErrorMessage() renders this string directly.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=resp.message or resp.error or "Failed to start live chat.",
        )
    return resp


@router.post("/stop", response_model=LiveStartResponse)
async def stop_live_chat(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveStartResponse:
    session = await get_owned_session(db, current_user)
    manager = _manager_for(session)
    result = await manager.stop()
    return LiveStartResponse(**result)


@router.get("/status", response_model=LiveStartResponse)
async def live_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveStartResponse:
    session = await get_owned_session(db, current_user)
    return _snapshot_response(_manager_for(session))


# ── Chat list ────────────────────────────────────────────────────────────────


@router.get("/chats", response_model=LiveChatListResponse)
async def list_live_chats(
    q: Optional[str] = Query(None, description="Filter chats via WhatsApp search"),
    limit: int = Query(DEFAULT_CHAT_LIMIT, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveChatListResponse:
    manager = await _require_running(db, current_user)

    chats = await manager.list_chats(filter_text=q, limit=limit)
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
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveOpenChatResponse:
    manager = await _require_running(db, current_user)
    result = await manager.open_chat(payload.chat_id)
    if not result.get("ok"):
        return LiveOpenChatResponse(ok=False, error=result.get("error"))
    return LiveOpenChatResponse(
        ok=True,
        chat_id=result.get("chat_id"),
        name=result.get("name"),
    )


@router.post("/chats/close", response_model=LiveOpenChatResponse)
async def close_live_chat(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveOpenChatResponse:
    manager = await _require_running(db, current_user)
    result = await manager.close_active_chat()
    return LiveOpenChatResponse(**result)


# ── Reading / writing ────────────────────────────────────────────────────────


@router.get("/messages", response_model=LiveMessagesResponse)
async def get_live_messages(
    limit: int = Query(DEFAULT_MESSAGE_LIMIT, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveMessagesResponse:
    manager = await _require_running(db, current_user)
    if not manager.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No chat is currently open. POST /live/chats/open first."
            ),
        )

    try:
        messages = await manager.read_messages(limit=limit)
        items = [LiveMessageItem(**m) for m in messages]
        return LiveMessagesResponse(
            chat_id=manager.active_chat_id,
            chat_name=manager.active_chat_name,
            messages=items,
            count=len(items),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Failed to read WhatsApp live messages (active_chat_id=%r)",
            manager.active_chat_id,
        )
        detail = str(exc).strip() or exc.__class__.__name__
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read messages: {detail}",
        ) from exc


@router.post("/messages/send", response_model=LiveSendResponse)
async def send_live_message(
    payload: LiveSendRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LiveSendResponse:
    manager = await _require_running(db, current_user)
    if not manager.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open a chat before sending.",
        )

    # send_message itself inserts the throttle sleep before the click; we
    # don't need to add another one here. Run synchronously so the response
    # reflects the actual send outcome (browser errors) and the cooldown
    # duration, both of which the frontend can render.
    t0 = asyncio.get_running_loop().time()
    result = await manager.send_message(payload.text)
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

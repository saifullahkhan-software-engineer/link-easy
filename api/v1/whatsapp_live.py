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
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from api.v1.live import sse, sse_response, sse_user
from api.v1.whatsapp_sessions import get_owned_session
from core.logging_config import get_logger
from database import async_session
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
    db: AsyncSession, current_user: User, session_id: Optional[int] = None
) -> WhatsAppSession:
    """Resolve the caller's connected session or raise a readable 400/404."""
    return await get_owned_session(
        db, current_user, require_connected=True, session_id=session_id
    )


def _manager_for(session: Optional[WhatsAppSession]):
    """The live-chat manager that owns ``session`` (legacy when None)."""
    return get_live_browser(getattr(session, "id", None) if session else None)


async def _require_running(db: AsyncSession, current_user: User, session_id: Optional[int] = None):
    """Return the caller's running manager or raise the uniform 409."""
    session = await get_owned_session(db, current_user, session_id=session_id)
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
    session_id: Optional[int] = Query(None, ge=1, description="Which session to open live chat from"),
) -> LiveStartResponse:
    session = await _require_connection(db, current_user, session_id)
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
    session_id: Optional[int] = Query(None, ge=1, description="Which session to stop"),
) -> LiveStartResponse:
    session = await get_owned_session(db, current_user, session_id=session_id)
    manager = _manager_for(session)
    result = await manager.stop()
    return LiveStartResponse(**result)


@router.get("/status", response_model=LiveStartResponse)
async def live_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: Optional[int] = Query(None, ge=1, description="Which session's live status"),
) -> LiveStartResponse:
    session = await get_owned_session(db, current_user, session_id=session_id)
    return _snapshot_response(_manager_for(session))


# ── Chat list ────────────────────────────────────────────────────────────────


@router.get("/chats", response_model=LiveChatListResponse)
async def list_live_chats(
    q: Optional[str] = Query(None, description="Filter chats via WhatsApp search"),
    limit: int = Query(DEFAULT_CHAT_LIMIT, ge=1, le=200),
    scroll: bool = Query(False, description="Scroll sidebar down to load next page of chats"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chats"),
) -> LiveChatListResponse:
    manager = await _require_running(db, current_user, session_id)

    chats = await manager.list_chats(filter_text=q, limit=limit, scroll=scroll)
    items = [LiveChatItem(**c) for c in chats]
    return LiveChatListResponse(
        chats=items,
        count=len(items),
        query=q,
        has_more=len(items) > 0 if scroll else True,
    )


# ── Active chat ──────────────────────────────────────────────────────────────


@router.post("/chats/open", response_model=LiveOpenChatResponse)
async def open_live_chat(
    payload: LiveOpenChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chats"),
) -> LiveOpenChatResponse:
    manager = await _require_running(db, current_user, session_id)
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
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chat"),
) -> LiveOpenChatResponse:
    manager = await _require_running(db, current_user, session_id)
    result = await manager.close_active_chat()
    return LiveOpenChatResponse(**result)


# ── Reading / writing ────────────────────────────────────────────────────────


@router.get("/messages/stream")
async def stream_live_messages(
    limit: int = Query(DEFAULT_MESSAGE_LIMIT, ge=1, le=200),
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chat stream"),
    current_user: User = Depends(sse_user),
):
    """Server-Sent Events (SSE) stream for real-time live WhatsApp messages.

    Emits an initial `snapshot` of the open conversation, followed by `append`
    events when new incoming or outgoing messages appear. Periodic ping
    heartbeats keep the socket alive.
    """
    async with async_session() as db:
        session = await get_owned_session(db, current_user, session_id=session_id)
        manager = _manager_for(session)

    if manager.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Live chat is not running. Call POST /live/start first.",
        )
    if not manager.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No chat is currently open. POST /live/chats/open first.",
        )

    async def event_generator():
        current_chat_id = manager.active_chat_id
        current_chat_name = manager.active_chat_name
        try:
            messages = await manager.read_live_messages_fast(limit=limit)
        except Exception as exc:
            logger.debug("Initial live WhatsApp stream read failed: %s", exc)
            messages = []

        known_ids = {
            m.get("whatsapp_message_id")
            for m in messages
            if m.get("whatsapp_message_id")
        }

        yield sse(
            "snapshot",
            {
                "chat_id": current_chat_id,
                "chat_name": current_chat_name,
                "messages": messages,
                "count": len(messages),
            },
        )

        last_ping = time.monotonic()
        try:
            while True:
                await asyncio.sleep(1.0)

                if manager.status != "running":
                    yield sse("status", manager.snapshot())
                    break

                if manager.active_chat_id != current_chat_id:
                    if not manager.active_chat_id:
                        yield sse("status", manager.snapshot())
                        break
                    current_chat_id = manager.active_chat_id
                    current_chat_name = manager.active_chat_name
                    try:
                        messages = await manager.read_live_messages_fast(limit=limit)
                    except Exception:
                        messages = []
                    known_ids = {
                        m.get("whatsapp_message_id")
                        for m in messages
                        if m.get("whatsapp_message_id")
                    }
                    yield sse(
                        "snapshot",
                        {
                            "chat_id": current_chat_id,
                            "chat_name": current_chat_name,
                            "messages": messages,
                            "count": len(messages),
                        },
                    )
                    continue

                try:
                    curr = await manager.read_live_messages_fast(limit=limit)
                except Exception:
                    curr = []

                new_messages = [
                    m
                    for m in curr
                    if m.get("whatsapp_message_id")
                    and m.get("whatsapp_message_id") not in known_ids
                ]

                if new_messages:
                    for m in new_messages:
                        known_ids.add(m["whatsapp_message_id"])
                    yield sse(
                        "append",
                        {
                            "chat_id": current_chat_id,
                            "messages": new_messages,
                        },
                    )

                if time.monotonic() - last_ping >= 15.0:
                    last_ping = time.monotonic()
                    yield ": ping\n\n"

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("WhatsApp live stream ended with error: %s", exc)
            yield sse("error", {"detail": str(exc)})

    return sse_response(event_generator())


@router.get("/messages", response_model=LiveMessagesResponse)
async def get_live_messages(
    limit: int = Query(DEFAULT_MESSAGE_LIMIT, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chat"),
) -> LiveMessagesResponse:
    manager = await _require_running(db, current_user, session_id)
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
    session_id: Optional[int] = Query(None, ge=1, description="Which session's chat"),
) -> LiveSendResponse:
    manager = await _require_running(db, current_user, session_id)
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

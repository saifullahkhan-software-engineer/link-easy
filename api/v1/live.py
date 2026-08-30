"""
Live browser view + API log streaming.

FILE: api/v1/live.py

GET   /api/v1/live/logs               → SSE stream of app logs (backend terminal is primary)
GET   /api/v1/live/browser/status     → browser view status (JSON)
POST  /api/v1/live/browser/start      → start the embedded headless browser
POST  /api/v1/live/browser/stop       → stop it
POST  /api/v1/live/browser/input      → dispatch click/scroll/type/key into it
GET   /api/v1/live/browser/frame      → latest screencast frame (image/jpeg)
GET   /api/v1/live/browser/stream     → SSE stream of status + frames

Authentication
--------------
EventSource cannot send ``Authorization`` headers, so the SSE endpoints and
the frame endpoint accept the access token as ``?token=...``.  The control
endpoints use the normal Bearer header via ``get_current_user``.

Note: In production, logs are written to the terminal/backend for easy
monitoring. The /api/v1/live/logs endpoint is available for debugging
when STREAM_LOGS_TO_FRONTEND=true is set. The browser view is ONLY opened
for QR scan and 2FA entry during WhatsApp connection.
"""
import asyncio
import base64
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_current_user_from_token, get_db
from database import async_session
from core.live_hub import log_hub
from models.user import User
from services.browser_view import WHATSAPP_URL, get_browser_view

router = APIRouter(prefix="/api/v1/live", tags=["live"])


async def _resolve_browser_view(user: User, db: AsyncSession):
    """Resolve the embedded browser view owned by ``user``'s WhatsApp session.

    Per-user rollout: every session owns its own browser view (QR connect
    screen), so control/stream endpoints must target the caller's view —
    never a process-wide singleton.
    """
    from api.v1.whatsapp_sessions import get_owned_session

    session = await get_owned_session(db, user)
    if session is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "WhatsApp is not connected on this account. "
                'Press "Connect WhatsApp" first.'
            ),
        )
    return get_browser_view(session.id)


# ── SSE helpers ──────────────────────────────────────────────────────────────


def sse(event: str, data: dict) -> str:
    """Serialize one Server-Sent-Events frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def sse_user(
    request: Request,
    token: str | None = Query(default=None),
) -> User:
    """Authenticate SSE clients via ``?token=`` or the Authorization header.

    IMPORTANT: this must NOT take ``Depends(get_db)``. An SSE connection stays
    open for the whole browser-view session (minutes), and a FastAPI yield
    dependency like ``get_db`` keeps its database connection checked out of
    the pool for that entire response — so every open live stream pinned one
    Postgres connection. With several streams open during a WhatsApp connect
    plus the status polling, the pool hit Railway's PgBouncer cap
    (EMAXCONNSESSION) and the whole app 500'd. Auth here uses a short-lived
    session that is closed before the stream generator starts streaming.
    """
    async with async_session() as db:
        if token:
            return await get_current_user_from_token(token, db)
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            return await get_current_user_from_token(auth[7:].strip(), db)
    raise HTTPException(status_code=401, detail="Not authenticated")


async def _resolve_view(user: User):
    """Resolve the caller's browser view using a short-lived DB session.

    Used by the SSE/frame endpoints so they never hold a pooled connection
    while the stream is open (see sse_user for why that mattered).
    """
    async with async_session() as db:
        return await _resolve_browser_view(user, db)


# ── Log stream ───────────────────────────────────────────────────────────────


@router.get("/logs")
async def stream_logs(request: Request, _: User = Depends(sse_user)):
    """SSE stream of API-call + application log events."""

    async def generator():
        queue = await log_hub.subscribe()
        try:
            # Replay recent history so late joiners aren't greeted with a
            # blank console.
            for event in log_hub.history(limit=100):
                yield sse(event.get("type", "log"), event)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield sse(event.get("type", "log"), event)
                except asyncio.TimeoutError:
                    yield sse("ping", {"t": time.time()})
        finally:
            await log_hub.unsubscribe(queue)

    return sse_response(generator())


# ── Browser view control ─────────────────────────────────────────────────────


@router.get("/browser/status")
async def browser_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """The caller's browser view status."""
    view = await _resolve_browser_view(current_user, db)
    return view.snapshot()


class BrowserStartRequest(BaseModel):
    url: str = WHATSAPP_URL


@router.post("/browser/start")
async def browser_start(
    payload: BrowserStartRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Launch (or reuse) the caller's embedded headless browser."""
    view = await _resolve_browser_view(current_user, db)
    url = (payload.url if payload and payload.url else WHATSAPP_URL).strip()
    result = await view.ensure_started(url)
    if result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or "Failed to start browser view",
        )
    return result


@router.post("/browser/stop")
async def browser_stop(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Stop the caller's embedded browser."""
    view = await _resolve_browser_view(current_user, db)
    return await view.stop()


class BrowserInputRequest(BaseModel):
    action: str = Field(..., pattern="^(click|scroll|type|key|navigate)$")
    x: float | None = Field(default=None, ge=0.0, le=1.0)
    y: float | None = Field(default=None, ge=0.0, le=1.0)
    deltaX: float | None = Field(default=None)
    deltaY: float | None = Field(default=None)
    text: str | None = Field(default=None)
    key: str | None = Field(default=None)
    code: str | None = Field(default=None)
    url: str | None = Field(default=None)


@router.post("/browser/input")
async def browser_input(
    payload: BrowserInputRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Dispatch an input event into the caller's view (click/scroll/type/key)."""
    view = await _resolve_browser_view(current_user, db)
    result = await view.send_input(payload.model_dump(exclude_none=True))
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Input failed"))
    return result


@router.get("/browser/frame")
async def browser_frame(
    user: User = Depends(sse_user),
) -> Response:
    """Latest screencast frame as a plain JPEG (polling fallback)."""
    view = await _resolve_view(user)
    frame = view.latest_frame()
    if not frame:
        raise HTTPException(
            status_code=404, detail="No frame yet — start the browser view first"
        )
    return Response(
        content=base64.b64decode(frame),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/browser/stream")
async def browser_stream(
    request: Request,
    user: User = Depends(sse_user),
):
    """SSE stream of the caller's browser status events + screencast frames."""

    # Resolved with a short-lived session (see sse_user/_resolve_view): the
    # stream itself must not hold a pooled DB connection for minutes.
    view = await _resolve_view(user)

    async def generator():
        queue = await view.subscribe()
        try:
            yield sse("status", {"type": "status", **view.snapshot()})
            latest = view.latest_frame()
            if latest:
                yield sse("frame", {"type": "frame", "data": latest})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield sse(event.get("type", "event"), event)
                except asyncio.TimeoutError:
                    yield sse("ping", {"t": time.time()})
        finally:
            await view.unsubscribe(queue)

    return sse_response(generator())

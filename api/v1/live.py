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
from core.live_hub import log_hub
from models.user import User
from services.browser_view import WHATSAPP_URL, browser_view

router = APIRouter(prefix="/api/v1/live", tags=["live"])


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
    db: AsyncSession = Depends(get_db),
) -> User:
    """Authenticate SSE clients via ``?token=`` or the Authorization header."""
    if token:
        return await get_current_user_from_token(token, db)
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return await get_current_user_from_token(auth[7:].strip(), db)
    raise HTTPException(status_code=401, detail="Not authenticated")


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
async def browser_status(_: User = Depends(get_current_user)) -> dict:
    """Current browser view status."""
    return browser_view.snapshot()


class BrowserStartRequest(BaseModel):
    url: str = WHATSAPP_URL


@router.post("/browser/start")
async def browser_start(
    payload: BrowserStartRequest | None = None,
    _: User = Depends(get_current_user),
) -> dict:
    """Launch (or reuse) the embedded headless browser."""
    url = (payload.url if payload and payload.url else WHATSAPP_URL).strip()
    result = await browser_view.ensure_started(url)
    if result.get("status") == "error":
        raise HTTPException(
            status_code=500,
            detail=result.get("error") or "Failed to start browser view",
        )
    return result


@router.post("/browser/stop")
async def browser_stop(_: User = Depends(get_current_user)) -> dict:
    """Stop the embedded browser."""
    return await browser_view.stop()


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
    payload: BrowserInputRequest, _: User = Depends(get_current_user)
) -> dict:
    """Dispatch an input event (click / scroll / type / key / navigate)."""
    result = await browser_view.send_input(payload.model_dump(exclude_none=True))
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("error", "Input failed"))
    return result


@router.get("/browser/frame")
async def browser_frame(_: User = Depends(sse_user)) -> Response:
    """Latest screencast frame as a plain JPEG (polling fallback)."""
    frame = browser_view.latest_frame()
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
async def browser_stream(request: Request, _: User = Depends(sse_user)):
    """SSE stream of browser status events + screencast JPEG frames."""

    async def generator():
        queue = await browser_view.subscribe()
        try:
            yield sse("status", {"type": "status", **browser_view.snapshot()})
            latest = browser_view.latest_frame()
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
            await browser_view.unsubscribe(queue)

    return sse_response(generator())

"""
LinkedIn live chat — REST API.

FILE: api/v1/linkedin_live.py

Mirror of ``api/v1/whatsapp_live.py`` but for the user's LinkedIn account.
Routes require ``get_current_user``; the singleton live browser holds the
``profile_lock("linkedin")`` Redis lock so the periodic scan task pauses
while a chat session is active.

Endpoints
---------
POST   /api/v1/linkedin/live/start             open the live browser
POST   /api/v1/linkedin/live/stop              close + release the lock
GET    /api/v1/linkedin/live/status            snapshot of the manager
GET    /api/v1/linkedin/live/chats?limit=      list conversations
POST   /api/v1/linkedin/live/chats/open        {chat_id} → click into chat
POST   /api/v1/linkedin/live/chats/close       back to the messaging list
GET    /api/v1/linkedin/live/messages?limit=   messages of the open chat
POST   /api/v1/linkedin/live/messages/send     {text} → type + click send
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.dependencies import get_current_user
from core.logging_config import get_logger
from models.user import User
from schemas.linkedin_live import (
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
from services.linkedin_live_browser import linkedin_live_browser, DEFAULT_CHATS_LIMIT

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/linkedin/live", tags=["linkedin-live"])


def _snapshot_response() -> LiveStartResponse:
    snap = linkedin_live_browser.snapshot()
    return LiveStartResponse(**snap)


def _ensure_running() -> None:
    """Resolve 409 if the live browser hasn't been started yet."""
    if linkedin_live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LinkedIn live chat is not running. Call POST /live/start first.",
        )


@router.post("/start", response_model=LiveStartResponse)
async def start_live(
    _user: User = Depends(get_current_user),
) -> LiveStartResponse:
    result = await linkedin_live_browser.start()
    resp = LiveStartResponse(**result)
    if resp.status == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=resp.message or "Could not start LinkedIn live chat",
        )
    return resp


@router.post("/stop", response_model=LiveStartResponse)
async def stop_live(
    _user: User = Depends(get_current_user),
) -> LiveStartResponse:
    result = await linkedin_live_browser.stop()
    return LiveStartResponse(**result)


@router.get("/status", response_model=LiveStartResponse)
async def live_status(
    _user: User = Depends(get_current_user),
) -> LiveStartResponse:
    return _snapshot_response()


@router.get("/chats", response_model=LiveChatListResponse)
async def list_chats(
    limit: int = Query(DEFAULT_CHATS_LIMIT, ge=1, le=200),
    _user: User = Depends(get_current_user),
) -> LiveChatListResponse:
    _ensure_running()
    chats = await linkedin_live_browser.list_chats(limit=limit)
    return LiveChatListResponse(
        chats=[LiveChatItem(**c) for c in chats],
        count=len(chats),
    )


@router.post("/chats/open", response_model=LiveOpenChatResponse)
async def open_chat(
    payload: LiveOpenChatRequest,
    _user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    _ensure_running()
    result = await linkedin_live_browser.open_chat(payload.chat_id)
    if not result.get("ok"):
        return LiveOpenChatResponse(ok=False, error=result.get("error"))
    return LiveOpenChatResponse(
        ok=True,
        chat_id=result.get("chat_id"),
        name=result.get("name"),
    )


@router.post("/chats/close", response_model=LiveOpenChatResponse)
async def close_chat(
    _user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    result = await linkedin_live_browser.close_active_chat()
    return LiveOpenChatResponse(**result)


@router.get("/messages", response_model=LiveMessagesResponse)
async def get_messages(
    limit: int = Query(50, ge=1, le=200),
    _user: User = Depends(get_current_user),
) -> LiveMessagesResponse:
    _ensure_running()
    if not linkedin_live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No chat is currently open. POST /live/chats/open first.",
        )
    messages = await linkedin_live_browser.read_messages(limit=limit)
    return LiveMessagesResponse(
        chat_id=linkedin_live_browser.active_chat_id,
        chat_name=linkedin_live_browser.active_chat_name,
        messages=[LiveMessageItem(**m) for m in messages],
        count=len(messages),
    )


@router.post("/messages/send", response_model=LiveSendResponse)
async def send_message(
    payload: LiveSendRequest,
    _user: User = Depends(get_current_user),
) -> LiveSendResponse:
    _ensure_running()
    if not linkedin_live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open a chat before sending a message.",
        )
    t0 = asyncio.get_running_loop().time()
    result = await linkedin_live_browser.send_message(payload.text)
    elapsed = asyncio.get_running_loop().time() - t0
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Send failed",
        )
    return LiveSendResponse(ok=True, throttled_seconds=round(elapsed, 2))

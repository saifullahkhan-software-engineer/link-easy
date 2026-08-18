"""Authenticated REST surface for the serialized LinkedIn live browser."""
from __future__ import annotations

from typing import Optional

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
from services.linkedin_live_browser import DEFAULT_CHAT_LIMIT, linkedin_live_browser

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/linkedin/live", tags=["linkedin-live"])


def _ensure_running(current_user: User) -> None:
    if linkedin_live_browser.status != "running":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="LinkedIn live chat is not running. Start it first.",
        )
    if not linkedin_live_browser.is_owned_by(current_user.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different account is currently using LinkedIn live chat.",
        )


@router.post("/start", response_model=LiveStartResponse)
async def start_live(
    current_user: User = Depends(get_current_user),
) -> LiveStartResponse:
    result = await linkedin_live_browser.start(current_user.email)
    response = LiveStartResponse(**result)
    if response.status == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.message or "Could not start LinkedIn live chat.",
        )
    return response


@router.post("/stop", response_model=LiveStartResponse)
async def stop_live(
    current_user: User = Depends(get_current_user),
) -> LiveStartResponse:
    if (
        linkedin_live_browser.owner_email
        and not linkedin_live_browser.is_owned_by(current_user.email)
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A different account is currently using LinkedIn live chat.",
        )
    return LiveStartResponse(**(await linkedin_live_browser.stop()))


@router.get("/status", response_model=LiveStartResponse)
async def live_status(
    current_user: User = Depends(get_current_user),
) -> LiveStartResponse:
    if (
        linkedin_live_browser.owner_email
        and not linkedin_live_browser.is_owned_by(current_user.email)
    ):
        return LiveStartResponse(
            status="idle",
            message="LinkedIn live chat is not running for this account.",
        )
    return LiveStartResponse(**linkedin_live_browser.snapshot())


@router.get("/chats", response_model=LiveChatListResponse)
async def list_chats(
    limit: int = Query(DEFAULT_CHAT_LIMIT, ge=1, le=100),
    q: Optional[str] = Query(None, max_length=100),
    current_user: User = Depends(get_current_user),
) -> LiveChatListResponse:
    _ensure_running(current_user)
    try:
        chats = await linkedin_live_browser.list_chats(limit=100 if q else limit)
        if q:
            needle = q.casefold().strip()
            chats = [
                chat
                for chat in chats
                if needle in (chat.get("name") or "").casefold()
                or needle in (chat.get("preview") or "").casefold()
            ][:limit]
        items = [LiveChatItem(**chat) for chat in chats]
        return LiveChatListResponse(chats=items, count=len(items))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not list LinkedIn chats")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not list LinkedIn chats: {str(exc).strip() or exc.__class__.__name__}",
        ) from exc


@router.post("/chats/open", response_model=LiveOpenChatResponse)
async def open_chat(
    payload: LiveOpenChatRequest,
    current_user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    _ensure_running(current_user)
    result = await linkedin_live_browser.open_chat(payload.chat_id)
    return LiveOpenChatResponse(**result)


@router.post("/chats/close", response_model=LiveOpenChatResponse)
async def close_chat(
    current_user: User = Depends(get_current_user),
) -> LiveOpenChatResponse:
    _ensure_running(current_user)
    return LiveOpenChatResponse(**(await linkedin_live_browser.close_active_chat()))


@router.get("/messages", response_model=LiveMessagesResponse)
async def get_messages(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
) -> LiveMessagesResponse:
    _ensure_running(current_user)
    if not linkedin_live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No chat is currently open. Open a conversation first.",
        )
    try:
        messages = await linkedin_live_browser.read_messages(limit=limit)
        items = [LiveMessageItem(**message) for message in messages]
        return LiveMessagesResponse(
            chat_id=linkedin_live_browser.active_chat_id,
            chat_name=linkedin_live_browser.active_chat_name,
            messages=items,
            count=len(items),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "Could not read LinkedIn messages (active_chat_id=%r)",
            linkedin_live_browser.active_chat_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not read LinkedIn messages: {str(exc).strip() or exc.__class__.__name__}",
        ) from exc


@router.post("/messages/send", response_model=LiveSendResponse)
async def send_message(
    payload: LiveSendRequest,
    current_user: User = Depends(get_current_user),
) -> LiveSendResponse:
    _ensure_running(current_user)
    if not linkedin_live_browser.active_chat_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Open a chat before sending a message.",
        )
    result = await linkedin_live_browser.send_message(payload.text)
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error") or "Send failed.",
        )
    return LiveSendResponse(
        ok=True,
        throttled_seconds=float(result.get("throttled_seconds") or 0),
    )

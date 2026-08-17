"""
WhatsApp live chat — Pydantic schemas.

FILE: schemas/whatsapp_live.py

Used by api/v1/whatsapp_live.py to expose the live-chat manager to the
frontend (start/stop a session, list chats, read messages, send).
"""
from typing import Optional

from pydantic import BaseModel, Field


# ── Lifecycle ────────────────────────────────────────────────────────────────


class LiveStartResponse(BaseModel):
    """Returned by POST ``/live/start`` and GET ``/live/status``."""

    status: str = Field(
        ...,
        description=(
            "idle | starting | running | error. 'error' usually means WhatsApp "
            "is not connected, the browser is busy, or the saved session expired."
        ),
    )
    message: str
    error: Optional[str] = None
    active_chat_id: Optional[str] = None
    active_chat_name: Optional[str] = None


# ── Chat list ────────────────────────────────────────────────────────────────


class LiveChatItem(BaseModel):
    chat_id: str
    name: str
    preview: Optional[str] = None
    unread_count: int = 0


class LiveChatListResponse(BaseModel):
    chats: list[LiveChatItem]
    count: int
    query: Optional[str] = None


# ── Open chat ────────────────────────────────────────────────────────────────


class LiveOpenChatRequest(BaseModel):
    chat_id: str


class LiveOpenChatResponse(BaseModel):
    ok: bool
    chat_id: Optional[str] = None
    name: Optional[str] = None
    error: Optional[str] = None


# ── Messages ─────────────────────────────────────────────────────────────────


class LiveMessageItem(BaseModel):
    # WhatsApp-internal message id (data-id) when available.
    whatsapp_message_id: Optional[str] = None
    # Who sent it (None when you sent it yourself — rely on ``is_outgoing``).
    sender: Optional[str] = None
    # Plain-text body. Image-only messages have empty ``text``.
    text: str = ""
    # text | image | ... (mirrors WhatsAppRawMessage.message_type values).
    type: str = "text"
    # True when the chat owner sent it, False when received.
    is_outgoing: bool = False
    # Best-effort timestamp — WhatsApp exposes HH:MM inline, so for live
    # messages we use the page-render ordering rather than a calendar time.
    timestamp: Optional[int] = None


class LiveMessagesResponse(BaseModel):
    chat_id: Optional[str] = None
    chat_name: Optional[str] = None
    messages: list[LiveMessageItem]
    count: int


# ── Send ─────────────────────────────────────────────────────────────────────


class LiveSendRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class LiveSendResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    # Seconds the server waited before actually sending (anti-block pacing).
    throttled_seconds: float = 0.0

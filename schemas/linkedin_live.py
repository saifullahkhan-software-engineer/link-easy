"""
LinkedIn live chat — Pydantic schemas.

FILE: schemas/linkedin_live.py
"""
from typing import Optional

from pydantic import BaseModel, Field


class LiveStartResponse(BaseModel):
    status: str
    message: str
    error: Optional[str] = None
    active_chat_id: Optional[str] = None
    active_chat_name: Optional[str] = None


class LiveChatItem(BaseModel):
    chat_id: str
    name: str
    preview: Optional[str] = None
    unread_count: int = 0


class LiveChatListResponse(BaseModel):
    chats: list[LiveChatItem]
    count: int


class LiveOpenChatRequest(BaseModel):
    chat_id: str


class LiveOpenChatResponse(BaseModel):
    ok: bool
    chat_id: Optional[str] = None
    name: Optional[str] = None
    error: Optional[str] = None


class LiveMessageItem(BaseModel):
    whatsapp_message_id: Optional[str] = None  # LinkedIn doesn't expose a stable id
    sender: Optional[str] = None
    text: str = ""
    type: str = "text"
    is_outgoing: bool = False
    timestamp: Optional[int] = None


class LiveMessagesResponse(BaseModel):
    chat_id: Optional[str] = None
    chat_name: Optional[str] = None
    messages: list[LiveMessageItem]
    count: int


class LiveSendRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class LiveSendResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    throttled_seconds: float = 0.0

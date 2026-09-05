"""
Gmail — API schemas.
FILE: schemas/gmail.py

Request/response shapes for the Gmail router (api/v1/gmail.py). Message
*parsing* (Google's JSON → these shapes) lives in services/gmail.py; this file
only declares the wire contract.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── connection / OAuth ────────────────────────────────────────────────────────

class GmailStatus(BaseModel):
    connected: bool = False
    # True when the operator set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET, so
    # the UI can distinguish "not connected yet" from "cannot ever connect
    # on this instance".
    configured: bool = False
    account_email: str = ""
    scopes: list[str] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
    reconnect_required: bool = False
    messages_total: Optional[int] = None
    last_checked_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class GmailProfileResponse(BaseModel):
    email_address: str = ""
    messages_total: int = 0
    threads_total: int = 0
    history_id: str = ""
    fetched_at: datetime


class GmailAuthUrlResponse(BaseModel):
    auth_url: str


class GmailMessageResponse(BaseModel):
    message: str


# ── mailbox data ──────────────────────────────────────────────────────────────

class GmailLabel(BaseModel):
    id: str
    name: str
    type: str  # system | user
    messages_total: Optional[int] = None
    messages_unread: Optional[int] = None


class GmailAddress(BaseModel):
    name: str = ""
    email: str = ""


class GmailAttachment(BaseModel):
    attachment_id: str
    filename: str
    mime_type: str = "application/octet-stream"
    size: int = 0


class GmailMessageSummary(BaseModel):
    id: str
    thread_id: str = ""
    label_ids: list[str] = Field(default_factory=list)
    subject: str = ""
    from_name: str = ""
    from_email: str = ""
    snippet: str = ""
    date: str = ""
    internal_date: Optional[datetime] = None
    is_read: bool = True
    is_starred: bool = False


class GmailMessageDetail(GmailMessageSummary):
    to: list[GmailAddress] = Field(default_factory=list)
    cc: list[GmailAddress] = Field(default_factory=list)
    bcc: list[GmailAddress] = Field(default_factory=list)
    reply_to: list[GmailAddress] = Field(default_factory=list)
    message_id_header: str = ""
    text_body: str = ""
    html_body: str = ""
    attachments: list[GmailAttachment] = Field(default_factory=list)
    size_estimate: int = 0


class GmailMessageListResponse(BaseModel):
    messages: list[GmailMessageSummary] = Field(default_factory=list)
    next_page_token: str = ""
    result_size_estimate: int = 0
    label_id: str = ""
    q: str = ""


class GmailThreadResponse(BaseModel):
    id: str
    messages: list[GmailMessageDetail] = Field(default_factory=list)


class GmailUnreadResponse(BaseModel):
    unread_in_inbox: int = 0
    inbox_total: int = 0
    messages: list[GmailMessageSummary] = Field(default_factory=list)
    checked_at: Optional[datetime] = None


# ── actions ───────────────────────────────────────────────────────────────────

class GmailModifyRequest(BaseModel):
    add_label_ids: list[str] = Field(default_factory=list)
    remove_label_ids: list[str] = Field(default_factory=list)


class GmailSendRequest(BaseModel):
    # Comma-separated recipient lists, e.g. "a@x.com, B Name <b@x.com>".
    # Presence-only: emptiness is rejected in the route with a 400 so the
    # client gets the same message whether "to" is missing or blank.
    to: str = ""
    cc: str = ""
    bcc: str = ""
    subject: str = Field(max_length=998)
    body: str = Field(max_length=500_000)
    # Threading metadata for replies (from the message being replied to).
    in_reply_to: str = ""
    references: str = ""


class GmailSendResponse(BaseModel):
    id: str = ""
    thread_id: str = ""
    to: str = ""
    subject: str = ""


__all__ = [
    "GmailAddress",
    "GmailAttachment",
    "GmailAuthUrlResponse",
    "GmailLabel",
    "GmailMessageDetail",
    "GmailMessageListResponse",
    "GmailMessageResponse",
    "GmailMessageSummary",
    "GmailModifyRequest",
    "GmailProfileResponse",
    "GmailSendRequest",
    "GmailSendResponse",
    "GmailStatus",
    "GmailThreadResponse",
    "GmailUnreadResponse",
]

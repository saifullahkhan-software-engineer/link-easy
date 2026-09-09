"""Assistant API payloads — what crosses the wire, and nothing more.

Provider keys, raw tool JSON and inbox message bodies never appear here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Channels the widget knows how to render as cards. Fixed vocabulary — the
# tool layer produces exactly these keys.
ChannelKey = Literal["instagram", "messenger", "whatsapp", "gmail"]


class AssistantChatRequest(BaseModel):
    """POST /assistant/chat — one user message."""

    model_config = ConfigDict(extra="forbid")

    # The user's own words. Longer than a screenful is a paste, not a chat.
    message: str = Field(min_length=1, max_length=2000)
    # The page the widget is mounted on (the "system overlay") — a frontend
    # route path, validated to shape, resolved to a title server-side.
    current_path: Optional[str] = Field(default=None, max_length=200)
    # Continue an existing conversation; omit to start a new one.
    conversation_id: Optional[str] = Field(default=None, min_length=8, max_length=64)

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Write a message first")
        return text

    @field_validator("current_path")
    @classmethod
    def validate_current_path(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        path = value.strip()
        if not path:
            return None
        # A route path, not a URL: no scheme, no host, no query — the value
        # is only ever matched against the corpus route index.
        if not path.startswith("/") or "://" in path or "?" in path or "#" in path:
            raise ValueError("current_path must be an app route like /app/gmail")
        return path[:200]


class AssistantConversationItem(BaseModel):
    """One row of the conversation list."""

    id: str
    title: str = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AssistantConversationsResponse(BaseModel):
    conversations: list[AssistantConversationItem]


class AssistantAction(BaseModel):
    """Something the widget should offer/do.

    ``navigate`` moves to a page; ``open_chat`` additionally opens one chat
    and types a draft into its message box (the inbox page consumes
    ``conversation_id``/``draft``) so the user reviews before anything sends.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["navigate", "open_chat"]
    path: str
    label: str = ""
    reason: str = ""
    # True → the user explicitly asked to go there; the widget auto-navigates
    # (unless the user switched auto-navigation off). False → render a button.
    auto: bool = False
    # open_chat only: which chat to open and what to pre-fill.
    channel: Optional[str] = None
    conversation_id: Optional[str] = None
    conversation_name: str = ""
    draft: str = ""


class AssistantChannelConversation(BaseModel):
    """One latest conversation/email inside a channel summary."""

    name: str = ""
    preview: str = ""
    updated_at: Optional[str] = None


class AssistantChannelSummary(BaseModel):
    """Per-channel result of check_new_messages, for the widget's cards."""

    channel: ChannelKey
    label: str = ""
    path: str = ""
    connected: bool = False
    status: Optional[str] = None  # ok | not_running | reconnect_required | error
    error: Optional[str] = None
    hint: Optional[str] = None
    unread_count: Optional[int] = None
    total_conversations: Optional[int] = None
    reconnect_required: Optional[bool] = None
    conversations: list[AssistantChannelConversation] = []
    accounts: Optional[list[dict[str, Any]]] = None


class AssistantMessageItem(BaseModel):
    """One stored turn, as the history endpoint returns it."""

    id: int
    role: Literal["user", "assistant"]
    content: str
    actions: list[AssistantAction] = []
    created_at: Optional[datetime] = None


class AssistantHistoryResponse(BaseModel):
    conversation_id: str
    title: str = ""
    messages: list[AssistantMessageItem]


class AssistantChatResponse(BaseModel):
    """POST /assistant/chat — the assistant's turn."""

    conversation_id: str
    reply: str
    actions: list[AssistantAction] = []
    channels: Optional[list[AssistantChannelSummary]] = None
    created_at: Optional[datetime] = None


__all__ = [
    "AssistantAction",
    "AssistantChannelConversation",
    "AssistantChannelSummary",
    "AssistantChatRequest",
    "AssistantChatResponse",
    "AssistantConversationItem",
    "AssistantConversationsResponse",
    "AssistantHistoryResponse",
    "AssistantMessageItem",
]

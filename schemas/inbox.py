"""Public inbox payloads. Provider tokens and Graph paging URLs never leave the API."""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class InboxConversation(BaseModel):
    id: str
    name: str
    preview: str = ""
    updated_at: str | None = None


class InboxConversationsResponse(BaseModel):
    conversations: list[InboxConversation]
    next_cursor: str | None = None


class InboxMessage(BaseModel):
    id: str
    text: str = ""
    sender: str = ""
    outgoing: bool = False
    created_at: str | None = None


class InboxMessagesResponse(BaseModel):
    messages: list[InboxMessage]


class InboxReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # IDs are opaque, not URLs, and are encoded again before Graph requests.
    conversation_id: str = Field(min_length=1, max_length=2048)
    text: str = Field(min_length=1, max_length=1000)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Write a message before sending")
        # Conservative shared limit for text-only Instagram/Messenger replies.
        if len(text.encode("utf-8")) > 1000:
            raise ValueError("Replies must be at most 1,000 UTF-8 bytes")
        return text


class InboxReplyResponse(BaseModel):
    message_id: str

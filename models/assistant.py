"""AI assistant conversations — SQLAlchemy models.

FILE: models/assistant.py

Chat history for the in-app assistant. Two tables:

  * ``assistant_conversations`` — one row per conversation, owned by the user
    (``owner_email``, the same ownership key every other user-owned table
    uses). ``title`` is the first message, trimmed, so the sidebar/history
    list is readable without loading messages.
  * ``assistant_messages`` — the turns. ``role`` is ``user`` | ``assistant``
    | ``tool``. Tool traffic (the model's calls and the JSON results, which
    can contain message previews from the user's inboxes) is persisted for
    debugging and auditing but is NEVER replayed to the model and is not
    returned by the history endpoint — earlier turns contribute only their
    user/assistant text.

No secrets are stored here: provider keys live in settings, tokens stay in
their own encrypted columns, and message previews are capped at the tool
layer before they ever reach a row.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.sql import func

from database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    __table_args__ = (Index("ix_assistant_conversations_owner_updated", "owner_email", "updated_at"),)

    id = Column(String, primary_key=True, default=_uuid)
    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    # First user message, clipped — display only.
    title = Column(String, nullable=False, default="", server_default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    __table_args__ = (Index("ix_assistant_messages_conversation", "conversation_id", "id"),)

    # Monotonic integer key: a turn's user and assistant rows are written
    # within the same second on fast databases, and ``created_at`` alone
    # cannot order them — history replay and display both order by id.
    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(
        String,
        ForeignKey("assistant_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # user | assistant | tool
    role = Column(String, nullable=False)
    # For user/assistant: the visible text. For tool: the tool name + JSON
    # result (audit only — never replayed to the model or shown raw).
    content = Column(Text, nullable=False, default="", server_default="")
    # Assistant rows only: the actions returned to the widget (navigate
    # buttons etc.) as JSON, so history restores them.
    actions_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


__all__ = ["AssistantConversation", "AssistantMessage"]

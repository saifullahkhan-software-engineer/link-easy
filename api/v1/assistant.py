"""AI Assistant API — chat, history, conversations.

FILE: api/v1/assistant.py

  POST /api/v1/assistant/chat          {message, current_path?, conversation_id?}
      → the assistant's reply + navigation actions + channel summary
  GET  /api/v1/assistant/conversations → the caller's conversations (recent first)
  GET  /api/v1/assistant/conversations/{id} → one conversation's user/assistant turns
  DELETE /api/v1/assistant/conversations/{id}

Ownership: every route resolves rows by ``owner_email == current_user.email``;
another user's conversation id is a plain 404. Chat is rate-limited
(``assistant:chat``) because each message spends third-party AI tokens and,
when the model checks messages, a handful of provider API calls.

Provider availability: no configured key → 503 with a friendly message the
widget shows once; provider transport/auth trouble → 502/504/429 as raised.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_current_user, get_db
from api.rate_limit_deps import rate_limit
from models.assistant import AssistantConversation, AssistantMessage
from models.user import User
from schemas.assistant import (
    AssistantAction,
    AssistantChannelSummary,
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantConversationItem,
    AssistantConversationsResponse,
    AssistantHistoryResponse,
    AssistantMessageItem,
)
from services.ai.assistant import service as assistant_service
from services.ai.assistant.providers import AssistantProviderError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/assistant", tags=["ai-assistant"])

MAX_CONVERSATIONS_LISTED = 30


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post("/chat", response_model=AssistantChatResponse)
async def chat(
    payload: AssistantChatRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    _rate: None = Depends(rate_limit("assistant:chat")),
):
    """One assistant turn. The widget calls this for every user message."""
    _no_store(response)
    try:
        conversation = await assistant_service.get_owned_conversation(
            db, current_user, payload.conversation_id
        )
    except assistant_service.AssistantError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    try:
        # The turn runs BEFORE anything is stored, so the history the model
        # replays is exactly the prior turns — the current message is passed
        # separately. Both rows are written afterwards, user first, giving
        # the monotonic id order the history endpoint relies on.
        reply, actions, channels = await assistant_service.run_assistant_turn(
            db, current_user, conversation, payload.message, payload.current_path
        )
        await assistant_service.store_message(db, conversation, "user", payload.message)
        await assistant_service.store_message(db, conversation, "assistant", reply, actions)
        if not conversation.title:
            conversation.title = payload.message[:80]
        await db.commit()
    except AssistantProviderError as exc:
        await db.rollback()
        # Provider bodies/details stay in the log; the user gets the clean message.
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        logger.exception("assistant chat failed (user=%s)", current_user.email)
        raise HTTPException(status_code=502, detail="The assistant hit an unexpected error. Try again.") from None

    return AssistantChatResponse(
        conversation_id=conversation.id,
        reply=reply,
        actions=[AssistantAction(**action) for action in actions],
        channels=[AssistantChannelSummary(**channel) for channel in channels] if channels else None,
        created_at=None,
    )


@router.get("/conversations", response_model=AssistantConversationsResponse)
async def list_conversations(
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The caller's conversations, most recently updated first."""
    _no_store(response)
    rows = (
        await db.execute(
            select(AssistantConversation)
            .where(AssistantConversation.owner_email == current_user.email)
            .order_by(AssistantConversation.updated_at.desc(), AssistantConversation.id.desc())
            .limit(MAX_CONVERSATIONS_LISTED)
        )
    ).scalars().all()
    return AssistantConversationsResponse(
        conversations=[
            AssistantConversationItem(
                id=row.id, title=row.title, created_at=row.created_at, updated_at=row.updated_at
            )
            for row in rows
        ]
    )


async def _owned_conversation(db: AsyncSession, user: User, conversation_id: str) -> AssistantConversation:
    conversation = (
        await db.execute(
            select(AssistantConversation).where(
                AssistantConversation.id == conversation_id,
                AssistantConversation.owner_email == user.email,
            )
        )
    ).scalars().first()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.get("/conversations/{conversation_id}", response_model=AssistantHistoryResponse)
async def conversation_history(
    conversation_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One conversation's visible turns (user + assistant; tool traffic stays internal)."""
    _no_store(response)
    conversation = await _owned_conversation(db, current_user, conversation_id)
    history = await assistant_service.load_history(db, conversation, limit=200)
    messages = []
    for row in history:
        actions = []
        if row.actions_json:
            try:
                actions = [AssistantAction(**a) for a in json.loads(row.actions_json)]
            except (ValueError, TypeError):
                actions = []
        messages.append(
            AssistantMessageItem(
                id=row.id, role=row.role, content=row.content, actions=actions, created_at=row.created_at
            )
        )
    return AssistantHistoryResponse(
        conversation_id=conversation.id, title=conversation.title, messages=messages
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one of the caller's conversations and its messages."""
    _no_store(response)
    conversation = await _owned_conversation(db, current_user, conversation_id)
    await db.execute(
        AssistantMessage.__table__.delete().where(AssistantMessage.conversation_id == conversation.id)
    )
    await db.delete(conversation)
    await db.commit()
    return {"ok": True}


__all__ = ["router"]

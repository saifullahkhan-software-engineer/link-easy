"""Assistant tools — the capabilities the model can call.

FILE: services/ai/assistant/tools.py

Each tool is one entry in ``TOOL_SPECS`` (the OpenAI function-calling schema
the model sees) plus one async function in ``TOOL_IMPLEMENTATIONS``. The
service loop executes whatever the model picks; adding a capability later
(analytics, weather, reply drafting…) means adding one entry to both — nothing
else in the system changes.

Boundaries every tool respects:

* **Ownership** — every query filters on ``owner_email == current_user.email``;
  a tool can only ever see the caller's own rows.
* **Per-channel failure isolation** — ``check_new_messages`` fans out to four
  different providers (Meta, the WhatsApp browser, Google); one failing
  channel is reported as a failed channel, never a failed tool call.
* **Untrusted data stays data** — message previews are third-party text. They
  are truncated, JSON-encoded into the tool result (so they arrive quoted,
  not interpreted) and the system prompt instructs the model to treat tool
  output as data, never instructions.
* **``navigate`` never executes on the backend** — a backend cannot move a
  browser. It validates the path against the knowledge corpus and records an
  action the API returns to the widget, which owns the actual routing.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.gmail import GmailConnection
from models.social_scheduler import SocialPlatformConnection
from models.user import User
from services.ai.assistant import knowledge
from services.ai.assistant.context import build_user_snapshot, render_snapshot

logger = logging.getLogger(__name__)

#: Previews sent to the model are capped here — enough to recognise a
#: conversation, short enough that a whole channel summary stays tiny and a
#: prompt-injection attempt inside a preview has little room to work with.
PREVIEW_LIMIT = 140
#: Conversations/emails surfaced per channel. The assistant summarises; the
#: full list is one click away in the channel page.
PREVIEW_ITEMS = 3
#: Concurrent provider fan-out inside check_new_messages.
CHANNEL_CONCURRENCY = 4


class ToolError(Exception):
    """A tool rejected its arguments (the model should retry differently)."""


@dataclass
class ToolContext:
    """Per-message state shared by the tools and the service loop.

    ``actions`` collects navigation requests (returned to the widget);
    ``channels`` holds the rich per-channel summary from check_new_messages
    (also returned to the widget so it can render cards/buttons without the
    model having to list everything in prose).
    """

    db: AsyncSession
    user: User
    actions: list[dict[str, Any]] = field(default_factory=list)
    channels: list[dict[str, Any]] | None = None


# ── helpers ──────────────────────────────────────────────────────────────────


def _preview(text: Any, limit: int = PREVIEW_LIMIT) -> str:
    value = str(text or "").strip().replace("\n", " ")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _convo(name: Any, preview: Any, updated_at: Any = None) -> dict[str, Any]:
    return {
        "name": _preview(name, 80),
        "preview": _preview(preview),
        "updated_at": str(updated_at) if updated_at else None,
    }


# ── tool: check_new_messages ─────────────────────────────────────────────────


async def _check_meta_channel(ctx: ToolContext, platform: str, channel: str, path: str, label: str) -> dict[str, Any]:
    """Instagram / Messenger via the Meta Graph inbox service."""
    from services.social.connections import read_tokens, reconnect_required
    from services.social.inbox import InboxError, MetaInboxService

    entry: dict[str, Any] = {
        "channel": channel,
        "label": label,
        "path": path,
        "connected": False,
    }
    rows = (
        await ctx.db.execute(
            select(SocialPlatformConnection)
            .where(
                SocialPlatformConnection.owner_email == ctx.user.email,
                SocialPlatformConnection.platform == platform,
            )
            .order_by(SocialPlatformConnection.created_at.asc())
        )
    ).scalars().all()
    if not rows:
        return entry

    entry["connected"] = True
    conversations: list[dict[str, Any]] = []
    accounts_reported = 0
    errors: list[str] = []
    for connection in rows:
        account_name = connection.account_name or connection.account_id or label
        try:
            tokens = read_tokens(connection)
            if tokens.is_expired:
                errors.append(f"{account_name}: reconnect required")
                continue
            service = MetaInboxService(
                channel, connection.account_id or "", tokens.access_token,
                page_id=(connection.extra_data or {}).get("page_id", ""),
            )
            result = await service.list_conversations()
            rows_conversations = result.get("conversations") or []
            entry.setdefault("accounts", []).append(
                {"account": account_name, "conversations": len(rows_conversations)}
            )
            accounts_reported += 1
            for convo in rows_conversations[:PREVIEW_ITEMS]:
                conversations.append(_convo(convo.get("name"), convo.get("preview"), convo.get("updated_at")))
        except InboxError as exc:
            logger.info("assistant: %s inbox read failed for %s: %s", channel, account_name, exc)
            errors.append(f"{account_name}: {str(exc)[:120]}")
        except ValueError as exc:  # undecryptable tokens — key rotated
            errors.append(f"{account_name}: reconnect required")

    if accounts_reported:
        entry["status"] = "ok"
        entry["conversations"] = conversations
        entry["total_conversations"] = sum(a["conversations"] for a in entry.get("accounts", []))
    else:
        entry["status"] = "reconnect_required" if any("reconnect" in e for e in errors) else "error"
        entry["error"] = "; ".join(errors)[:200] or "no account could be read"
    if any(reconnect_required(connection) for connection in rows):
        entry["reconnect_required"] = True
    return entry


async def _check_whatsapp_channel(ctx: ToolContext) -> dict[str, Any]:
    """WhatsApp via the caller's live browser session (if one is running)."""
    from api.v1.whatsapp_sessions import get_owned_session

    try:
        from services.whatsapp_live_browser import get_live_browser
    except ImportError:  # slim installs without the browser stack
        return {
            "channel": "whatsapp",
            "label": "WhatsApp",
            "path": "/app/inbox/whatsapp",
            "connected": False,
            "status": "error",
            "error": "WhatsApp live chat is not available on this instance.",
        }

    entry: dict[str, Any] = {
        "channel": "whatsapp",
        "label": "WhatsApp",
        "path": "/app/inbox/whatsapp",
        "connected": False,
    }
    session = await get_owned_session(ctx.db, ctx.user, require_connected=False)
    if session is None or session.status != "connected" or not session.is_active:
        return entry
    entry["connected"] = True

    manager = get_live_browser(session.id)
    if manager.status != "running":
        entry["status"] = "not_running"
        entry["hint"] = "Open the WhatsApp inbox page once to start the live browser."
        return entry
    try:
        chats = await manager.list_chats(limit=PREVIEW_ITEMS + 2)
    except Exception as exc:  # the browser session died mid-read
        logger.info("assistant: whatsapp live read failed: %s", exc)
        entry["status"] = "error"
        entry["error"] = "The live WhatsApp browser could not be read."
        return entry
    entry["status"] = "ok"
    entry["total_conversations"] = len(chats)
    entry["conversations"] = [
        _convo(chat.get("name"), chat.get("preview")) for chat in chats[:PREVIEW_ITEMS]
    ]
    entry["unread_total"] = sum(int(chat.get("unread_count") or 0) for chat in chats)
    return entry


async def _check_gmail_channel(ctx: ToolContext) -> dict[str, Any]:
    """Gmail unread counts + newest unread mail, per connected mailbox."""
    from services.gmail import (
        GmailApiError,
        GmailService,
        apply_tokens,
        read_tokens,
        summarize_message,
    )

    entry: dict[str, Any] = {
        "channel": "gmail",
        "label": "Gmail",
        "path": "/app/gmail",
        "connected": False,
    }
    rows = (
        await ctx.db.execute(
            select(GmailConnection)
            .where(GmailConnection.owner_email == ctx.user.email)
            .order_by(GmailConnection.created_at.asc())
        )
    ).scalars().all()
    if not rows:
        return entry
    entry["connected"] = True

    service = GmailService()
    service.redirect_uri = ""
    mailboxes: list[dict[str, Any]] = []
    errors: list[str] = []
    total_unread = 0

    async def one(connection: GmailConnection) -> None:
        mailbox = connection.account_email or "Gmail"
        try:
            tokens = read_tokens(connection)
            if tokens.is_expired:
                renewed = await service.refresh_access_token(tokens.refresh_token)
                apply_tokens(
                    connection,
                    access_token=renewed.get("access_token") or "",
                    refresh_token=renewed.get("refresh_token") or tokens.refresh_token,
                    expires_in=renewed.get("expires_in"),
                )
                await ctx.db.commit()
                tokens = read_tokens(connection)
            inbox = await service.get_label(tokens.access_token, "INBOX")
            unread = int(inbox.get("messagesUnread") or 0)
            listing = await service.list_messages(
                tokens.access_token, q="in:inbox is:unread", max_results=PREVIEW_ITEMS
            )
            messages: list[dict[str, Any]] = []
            for row in listing.get("messages") or []:
                try:
                    summary = summarize_message(
                        await service.get_message_metadata(tokens.access_token, str(row.get("id")))
                    )
                except GmailApiError:
                    continue
                messages.append(_convo(summary.get("from_name") or summary.get("from_email"),
                                       summary.get("subject") or summary.get("snippet"),
                                       summary.get("date")))
            mailboxes.append({"account": mailbox, "unread": unread, "messages": messages[:PREVIEW_ITEMS]})
        except GmailApiError as exc:
            logger.info("assistant: gmail read failed for %s: %s", mailbox, exc)
            errors.append(f"{mailbox}: reconnect required" if exc.category == "auth" else f"{mailbox}: {str(exc)[:120]}")
        except ValueError:
            errors.append(f"{mailbox}: reconnect required")

    await asyncio.gather(*(one(connection) for connection in rows))
    if mailboxes:
        entry["status"] = "ok"
        entry["accounts"] = mailboxes
        total_unread = sum(m["unread"] for m in mailboxes)
        entry["unread_count"] = total_unread
        entry["conversations"] = [
            message for mailbox in mailboxes for message in mailbox["messages"]
        ][:PREVIEW_ITEMS]
    else:
        entry["status"] = "reconnect_required" if any("reconnect" in e for e in errors) else "error"
        entry["error"] = "; ".join(errors)[:200] or "no mailbox could be read"
    return entry


async def tool_check_new_messages(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Read every messaging channel the caller has connected, in parallel.

    One channel failing (expired token, dead browser, Google quota) never
    fails the call — the channel is reported with a ``status`` the model can
    explain, and the others still come back.
    """
    labels = {
        "instagram": "Instagram",
        "messenger": "Messenger",
        "whatsapp": "WhatsApp",
        "gmail": "Gmail",
    }
    paths = {
        "instagram": "/app/inbox/instagram",
        "messenger": "/app/inbox/messenger",
        "whatsapp": "/app/inbox/whatsapp",
        "gmail": "/app/gmail",
    }

    async def guarded(check, channel: str) -> dict[str, Any]:
        # Belt-and-braces isolation: the individual checks already catch their
        # own provider errors, but anything unexpected (an import, a schema
        # drift) degrades to one failed channel instead of a failed turn.
        try:
            return await check
        except Exception as exc:
            logger.exception("assistant: channel %s crashed during check", channel)
            return {
                "channel": channel,
                "label": labels[channel],
                "path": paths[channel],
                "connected": False,
                "status": "error",
                "error": f"could not read this channel: {type(exc).__name__}",
            }

    channels = await asyncio.gather(
        guarded(_check_meta_channel(ctx, "instagram", "instagram", "/app/inbox/instagram", "Instagram"), "instagram"),
        guarded(_check_meta_channel(ctx, "facebook", "messenger", "/app/inbox/messenger", "Messenger"), "messenger"),
        guarded(_check_whatsapp_channel(ctx), "whatsapp"),
        guarded(_check_gmail_channel(ctx), "gmail"),
    )
    result = {"channels": list(channels)}
    ctx.channels = list(channels)
    return result


# ── tool: list_connected_accounts ────────────────────────────────────────────


async def tool_list_connected_accounts(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """The caller's connected accounts across every integration."""
    from services.social.connections import reconnect_required

    social_rows = (
        await ctx.db.execute(
            select(SocialPlatformConnection)
            .where(SocialPlatformConnection.owner_email == ctx.user.email)
            .order_by(SocialPlatformConnection.created_at.asc())
        )
    ).scalars().all()
    gmail_rows = (
        await ctx.db.execute(
            select(GmailConnection).where(GmailConnection.owner_email == ctx.user.email)
        )
    ).scalars().all()
    snapshot = await build_user_snapshot(ctx.db, ctx.user)
    return {
        "social_accounts": [
            {
                "platform": row.platform,
                "account": row.account_name or row.account_id or "",
                "reconnect_required": reconnect_required(row),
            }
            for row in social_rows
        ],
        "gmail_mailboxes": [row.account_email for row in gmail_rows],
        "whatsapp_sessions": snapshot["connected_channels"]["whatsapp"],
        "linkedin_accounts": snapshot["connected_channels"]["linkedin"],
        "whatsapp_live_browser_running": snapshot["whatsapp_live_browser_running"],
    }


# ── tool: get_user_overview ──────────────────────────────────────────────────


async def tool_get_user_overview(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Counts of the caller's work: campaigns, scans, scheduled posts."""
    snapshot = await build_user_snapshot(ctx.db, ctx.user)
    return {"counts": snapshot["counts"], "channels": snapshot["connected_channels"]}


# ── tool: search_app_guide ───────────────────────────────────────────────────


async def tool_search_app_guide(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Retrieve guide sections (pages + how-tos) matching a query."""
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ToolError("query is required")
    sections = knowledge.retrieve(query, k=3)
    if not sections:
        return {"results": [], "note": "Nothing in the app guide matches — answer from general knowledge and say you are not sure."}
    return {
        "results": [
            {"title": section.title, "body": section.body[:800], "paths": section.paths}
            for section in sections
        ]
    }


# ── tool: navigate ───────────────────────────────────────────────────────────


async def tool_navigate(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """Record a navigation request for the frontend to execute.

    ``explicit`` is the model's judgement of whether the user *asked* to be
    taken there ("open my gmail") versus the assistant *suggesting* it. The
    widget auto-navigates only for explicit requests (and only when the user
    has not switched auto-navigation off); suggestions render as buttons.
    """
    raw_path = str(arguments.get("path") or "").strip()
    reason = _preview(arguments.get("reason"), 140)
    explicit = bool(arguments.get("explicit", False))
    if not raw_path:
        raise ToolError("path is required")

    resolved, label = knowledge.resolve_route(raw_path)
    if resolved is None:
        known = ", ".join(sorted(knowledge.ROUTE_INDEX)[:40])
        raise ToolError(f"'{raw_path}' is not a page in this app. Choose a real path. Known paths: {known}")

    ctx.actions.append(
        {
            "type": "navigate",
            "path": resolved,
            "label": label or resolved,
            "reason": reason,
            "auto": explicit,
        }
    )
    return {
        "recorded": True,
        "path": resolved,
        "label": label,
        "note": "The navigation will happen in the user's app when you finish replying."
        if explicit
        else "A button will be shown to the user when you finish replying.",
    }


# ── registry ─────────────────────────────────────────────────────────────────

TOOL_IMPLEMENTATIONS: dict[
    str, Callable[[ToolContext, dict[str, Any]], Awaitable[dict[str, Any]]]
] = {
    "check_new_messages": tool_check_new_messages,
    "list_connected_accounts": tool_list_connected_accounts,
    "get_user_overview": tool_get_user_overview,
    "search_app_guide": tool_search_app_guide,
    "navigate": tool_navigate,
}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_new_messages",
            "description": (
                "Check the user's new messages across all their connected channels "
                "(Instagram, Messenger/Facebook, WhatsApp, Gmail). Call this whenever the user "
                "asks about new/unread messages, their inbox, notifications, or who wrote to them. "
                "Returns per-channel status, counts and the latest conversations."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_connected_accounts",
            "description": (
                "List the accounts the user has connected (Instagram, Facebook/Messenger, Gmail, "
                "WhatsApp, LinkedIn, publishing socials) and which need reconnecting."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_user_overview",
            "description": (
                "Counts of the user's work in the app: campaigns, feed-scan jobs, scheduled "
                "posts, WhatsApp group filters, and which channels are connected."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_app_guide",
            "description": (
                "Search the app guide (every page and how-to) to answer 'where do I…', 'how do I…' "
                "questions accurately. Always prefer this over guessing a page or path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What the user wants to do or find, e.g. 'schedule a short video' or 'connect instagram'",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "navigate",
            "description": (
                "Send the user to a page in the app. Use explicit=true when the user clearly asked "
                "to open/go somewhere (auto-navigates); explicit=false to suggest a page (shows a button). "
                "path must be a real app path like /app/inbox/instagram."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "App path, e.g. /app/gmail"},
                    "reason": {"type": "string", "description": "Short reason shown to the user"},
                    "explicit": {"type": "boolean", "description": "Did the user explicitly ask to go there?"},
                },
                "required": ["path"],
            },
        },
    },
]


async def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Run one tool by name. ToolError propagates (the loop feeds it back)."""
    implementation = TOOL_IMPLEMENTATIONS.get(name)
    if implementation is None:
        raise ToolError(f"unknown tool: {name}")
    logger.info("assistant tool: name=%s user=%s", name, ctx.user.email)
    return await implementation(ctx, arguments)


def encode_tool_result(data: dict[str, Any]) -> str:
    """Tool result → the tool-message content (JSON, so text inside arrives quoted)."""
    return json.dumps(data, ensure_ascii=False, default=str)


__all__ = [
    "PREVIEW_ITEMS",
    "PREVIEW_LIMIT",
    "TOOL_IMPLEMENTATIONS",
    "TOOL_SPECS",
    "ToolContext",
    "ToolError",
    "encode_tool_result",
    "execute_tool",
    "render_snapshot",
]

"""Text conversations for Messenger and Instagram (Facebook Login).

Both channels use the Page-scoped Conversations/Send APIs, not the personal
Facebook inbox or Instagram Login API. Instagram's publishing connection
stores a user token plus a Page ID; resolve that Page's token in memory for
messaging and verify it still belongs to the connected Instagram account.

No messages or new credentials are persisted. We expose only the latest 20
messages (the documented Instagram conversation-detail limit), use bounded
requests, and never follow or expose Graph's token-bearing paging URLs.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

import aiohttp

from .meta_graph import GRAPH_API_BASE


class InboxError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _rows(edge: dict | None) -> list[dict]:
    data = (edge or {}).get("data") or []
    return [row for row in data if isinstance(row, dict)] if isinstance(data, list) else []


def _name(person: dict) -> str:
    return str(person.get("name") or person.get("username") or "Conversation")


class MetaInboxService:
    def __init__(self, channel: str, account_id: str, access_token: str, page_id: str = ""):
        if channel not in ("instagram", "messenger"):
            raise InboxError("Inbox channel not found", 404)
        self.channel = channel
        self.account_id = str(account_id)
        self.access_token = access_token
        self.page_id = str(page_id) if channel == "instagram" else self.account_id
        self.own_ids = {self.account_id, self.page_id} - {""}
        if not self.page_id or not self.account_id:
            raise InboxError("Reconnect this account in Accounts → Socials to link its Facebook Page.", 409)

    async def _request(self, session, node, *, token, edge="", method="GET", params=None, payload=None):
        # The host and edge are server-controlled. Never use a caller-supplied
        # URL, including a provider paging.next URL, for a credentialed request.
        if not node or node in (".", ".."):
            raise InboxError("Conversation not found", 404)
        url = f"{GRAPH_API_BASE}/{quote(str(node), safe='')}"
        if edge:
            url += f"/{edge}"
        try:
            async with session.request(
                method, url, params=params, json=payload,
                headers={"Authorization": f"Bearer {token}"}, allow_redirects=False,
            ) as response:
                data = await response.json()
                http_status = response.status
        except asyncio.TimeoutError as exc:
            raise InboxError("Meta took too long to respond. Refresh to try again.", 504) from exc
        except (aiohttp.ClientError, ValueError) as exc:
            # Raw transport/provider errors can contain credentialed URLs.
            raise InboxError("Could not reach Meta. Please try again.") from exc
        if not isinstance(data, dict):
            raise InboxError("Meta returned an unexpected response. Please try again.")
        error = data.get("error") or {}
        if error or http_status >= 300:
            code = error.get("code")
            if code == 190 or http_status == 401:
                raise InboxError("Meta access has expired. Reconnect the account in Accounts → Socials.", 409)
            if code in (4, 17, 32, 613) or http_status == 429:
                raise InboxError("Meta's messaging rate limit was reached. Wait a moment and try again.", 429)
            if code in (10, 200) or http_status == 403:
                raise InboxError(
                    "Meta has not allowed this messaging request. Reconnect in Accounts → Socials and approve "
                    "messaging permissions. The operator may need Meta App Review; replies must be within Meta's allowed window.",
                    403,
                )
            if code == 100 or http_status == 404:
                raise InboxError("Meta could not find or open this conversation. Refresh the inbox and try again.", 404)
            raise InboxError("Meta could not complete the messaging request. Please try again.")
        return data

    async def _page_token(self, session):
        if self.channel == "messenger":
            return self.access_token
        page = await self._request(
            session, self.page_id, token=self.access_token,
            params={"fields": "id,access_token,instagram_business_account"},
        )
        linked = page.get("instagram_business_account") or {}
        if str(linked.get("id") or "") != self.account_id or not page.get("access_token"):
            raise InboxError(
                "The linked Facebook Page is no longer available for this Instagram account. "
                "Reconnect Instagram in Accounts → Socials.", 409,
            )
        return page["access_token"]

    def _recipient(self, conversation):
        participants = _rows(conversation.get("participants"))
        # A Page token is already scoped by Meta, but also check the actual
        # conversation before reading/replying. Never accept a recipient ID
        # or another account's Page ID from the browser.
        if not any(str(p.get("id")) in self.own_ids for p in participants):
            raise InboxError("Conversation not found", 404)
        others = [p for p in participants if p.get("id") and str(p["id"]) not in self.own_ids]
        if len(others) != 1:
            raise InboxError("Only one-to-one conversations are supported in this inbox.", 409)
        return others[0]

    async def _check_channel(self, session, token, conversation_id, conversation):
        person = self._recipient(conversation)
        # A linked Page token may access both channels. Independently confirm
        # the requested conversation is in this channel on this Page.
        matches = await self._request(
            session, self.page_id, edge="conversations", token=token,
            params={"platform": self.channel, "user_id": str(person["id"]), "fields": "id"},
        )
        if not any(str(row.get("id")) == conversation_id for row in _rows(matches)):
            raise InboxError("Conversation not found", 404)
        return person

    async def list_conversations(self, after: str | None = None):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            token = await self._page_token(session)
            params = {
                "platform": self.channel,
                "fields": "id,updated_time,participants,messages.limit(1){id,message}",
                "limit": 25,
            }
            if after:
                params["after"] = after
            data = await self._request(session, self.page_id, edge="conversations", token=token, params=params)
        conversations = []
        for row in _rows(data):
            # The collection belongs to our Page. Ignore malformed entries;
            # opening/sending independently verifies membership again.
            if not row.get("id"):
                continue
            people = [p for p in _rows(row.get("participants")) if str(p.get("id")) not in self.own_ids]
            recent = _rows(row.get("messages"))
            conversations.append({
                "id": str(row["id"]),
                "name": _name(people[0]) if people else "Conversation",
                "preview": str(recent[0].get("message") or "") if recent else "",
                "updated_at": row.get("updated_time"),
            })
        paging = data.get("paging") or {}
        return {
            "conversations": conversations,
            "next_cursor": (paging.get("cursors") or {}).get("after") if paging.get("next") else None,
        }

    async def list_messages(self, conversation_id: str):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            token = await self._page_token(session)
            data = await self._request(
                session, conversation_id, token=token,
                params={"fields": "id,participants,messages.limit(20){id,created_time,from,to,message}"},
            )
            await self._check_channel(session, token, conversation_id, data)
        messages = []
        for row in _rows(data.get("messages")):
            if not row.get("id"):
                continue
            sender = row.get("from") or {}
            messages.append({
                "id": str(row["id"]),
                "text": str(row.get("message") or ""),
                "sender": _name(sender),
                "outgoing": str(sender.get("id")) in self.own_ids,
                "created_at": row.get("created_time"),
            })
        # Meta returns newest first; the chat reads oldest → newest.
        return {"messages": list(reversed(messages))}

    async def send_reply(self, conversation_id: str, text: str):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            token = await self._page_token(session)
            conversation = await self._request(
                session, conversation_id, token=token, params={"fields": "id,participants"},
            )
            person = await self._check_channel(session, token, conversation_id, conversation)
            payload = {"recipient": {"id": str(person["id"])}, "message": {"text": text}}
            if self.channel == "messenger":
                payload["messaging_type"] = "RESPONSE"
            # Never retry sends automatically: a lost response may still have
            # delivered the message. Meta enforces permission/window rules.
            sent = await self._request(
                session, self.page_id, edge="messages", token=token, method="POST", payload=payload,
            )
        if not sent.get("message_id"):
            raise InboxError("Meta did not confirm the message. Refresh the conversation before trying again.")
        return {"message_id": str(sent["message_id"])}

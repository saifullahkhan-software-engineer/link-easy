"""Dedicated, serialized LinkedIn browser manager for live chat and profile scans.

LinkedIn's messaging UI is a virtualized SPA and its CSS class names change
regularly.  This module keeps selectors scoped to the full-page messaging UI,
matches conversations against rows that were actually enumerated, and
serializes every operation that can navigate or mutate the shared page.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import Any, Optional
from urllib.parse import unquote, urlparse

from sqlalchemy import desc
from sqlalchemy.future import select

from automation.browser import launch_persistent_browser
from core.config import settings
from core.logging_config import get_logger
from database import async_session
from models.linkedin_account import LinkedInAccount, LinkedInAccountStatus

logger = get_logger(__name__)

MESSAGING_URL = "https://www.linkedin.com/messaging/"
LIVE_SEND_DELAY_SECONDS = float(getattr(settings, "LINKEDIN_LIVE_SEND_DELAY_SECONDS", 10))
DEFAULT_CHAT_LIMIT = 30
DEFAULT_MESSAGE_LIMIT = 50

# Full-page messaging selectors. Every generic textbox is scoped to main so a
# send can never target LinkedIn's global search or compact messaging overlay.
CHAT_LIST_ROOT_SELECTOR = (
    "main ul.msg-conversations-container__conversations-list, "
    "main .msg-conversations-container__conversations-list, "
    "main [data-view-name='messages-conversation-list'], "
    "main ul[aria-label*='conversation' i]"
)
CHAT_ROW_SELECTOR = (
    "li.msg-conversation-listitem, "
    "li[data-control-name='conversation_item'], "
    "[data-view-name='messages-conversation-list-item']"
)
CHAT_ROW_PAGE_SELECTOR = ", ".join(
    f"main {selector.strip()}" for selector in CHAT_ROW_SELECTOR.split(",")
)
CHAT_LINK_SELECTOR = (
    "a[href*='/messaging/thread/'], "
    "a[href*='/messaging/conversation/'], "
    "a.msg-conversation-listitem__link"
)
CHAT_NAME_SELECTOR = (
    ".msg-conversation-listitem__participant-names, "
    "h3.msg-conversation-listitem__participant-names, "
    "[data-anonymize='person-name'], "
    "span[dir='ltr']"
)
CHAT_PREVIEW_SELECTOR = (
    ".msg-conversation-card__message-snippet, "
    ".msg-conversation-listitem__message-snippet, "
    ".msg-conversation-card__message-snippet-body, "
    "p"
)
CHAT_UNREAD_SELECTOR = (
    ".notification-badge__count, "
    ".msg-conversation-listitem__unread-count, "
    "[aria-label*='unread' i]"
)
THREAD_PANEL_SELECTOR = (
    "main .msg-s-message-list-container, "
    "main .msg-thread, "
    "main .msg-convo-wrapper, "
    "main form.msg-form, "
    "main .msg-form"
)
MESSAGE_ROW_SELECTOR = (
    "main li.msg-s-message-list__event, "
    "main .msg-s-event-listitem, "
    "main li.msg-s-message-listitem, "
    "main [data-view-name='message-bubble']"
)
MESSAGE_TEXT_SELECTOR = (
    ".msg-s-event-listitem__body, "
    ".msg-s-message-listitem__body, "
    ".msg-s-event-listitem__message-bubble, "
    "[data-view-name='message-body'], "
    "p"
)
MESSAGE_SENDER_SELECTOR = (
    ".msg-s-message-group__name, "
    ".msg-s-event-listitem__name, "
    "[data-anonymize='person-name']"
)
MESSAGE_TIME_SELECTOR = "time, .msg-s-message-group__timestamp, [data-test-message-time]"
COMPOSER_SELECTOR = (
    "main .msg-form__contenteditable[contenteditable='true'], "
    "main form.msg-form div[contenteditable='true'][role='textbox'], "
    "main .msg-form div[contenteditable='true'][role='textbox']"
)
SEND_BUTTON_SELECTOR = (
    "main form.msg-form button[type='submit'], "
    "main button.msg-form__send-button, "
    "main button[aria-label*='Send' i]"
)


async def _short_text(element: Any, limit: int = 300) -> str:
    if element is None:
        return ""
    try:
        return (await element.inner_text()).strip()[:limit]
    except Exception:
        return ""


async def _attribute(element: Any, name: str) -> str:
    if element is None:
        return ""
    try:
        return (await element.get_attribute(name) or "").strip()
    except Exception:
        return ""


async def _first(element: Any, selector: str) -> Any:
    if element is None:
        return None
    try:
        return await element.query_selector(selector)
    except Exception:
        return None


def _chat_id_from_href(href: str) -> str:
    """Extract a LinkedIn thread id without interpolating it into CSS later."""
    if not href:
        return ""
    try:
        path = urlparse(href).path
    except Exception:
        path = href
    for marker in ("/messaging/thread/", "/messaging/conversation/"):
        if marker in path:
            value = path.split(marker, 1)[1].split("/", 1)[0]
            return unquote(value).strip()
    return ""


async def _row_identity(row: Any) -> tuple[str, str, str, str, int]:
    link = await _first(row, CHAT_LINK_SELECTOR)
    href = await _attribute(link, "href")
    chat_id = _chat_id_from_href(href)

    if not chat_id:
        for attr_name in ("data-conversation-id", "data-entity-urn", "data-urn", "id"):
            value = await _attribute(row, attr_name)
            if value:
                chat_id = value
                break

    name = await _short_text(await _first(row, CHAT_NAME_SELECTOR), 160)
    preview = await _short_text(await _first(row, CHAT_PREVIEW_SELECTOR), 300)
    if not name:
        name = chat_id or "LinkedIn conversation"
    if not chat_id:
        digest = hashlib.sha256(f"{name}\n{preview}".encode("utf-8")).hexdigest()[:20]
        chat_id = f"dom-{digest}"

    unread_text = await _short_text(await _first(row, CHAT_UNREAD_SELECTOR), 30)
    try:
        unread = int("".join(ch for ch in unread_text if ch.isdigit()) or "0")
    except ValueError:
        unread = 0
    return chat_id, name, preview, href, unread


class LinkedInLiveBrowserManager:
    """One process-local LinkedIn browser with serialized page operations."""

    def __init__(self) -> None:
        self.status: str = "idle"
        self.message: str = "LinkedIn live chat is not running."
        self.error: Optional[str] = None
        self.active_chat_id: Optional[str] = None
        self.active_chat_name: Optional[str] = None
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._account: Optional[LinkedInAccount] = None
        self._owner_email: Optional[str] = None
        self._profile_lock = None
        self._last_send_monotonic: float = 0.0
        self._lifecycle_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "message": self.message,
            "error": self.error,
            "active_chat_id": self.active_chat_id,
            "active_chat_name": self.active_chat_name,
        }

    @property
    def owner_email(self) -> Optional[str]:
        return self._owner_email

    def is_owned_by(self, owner_email: str) -> bool:
        return bool(self._owner_email and self._owner_email == owner_email)

    async def _cleanup_resources(self) -> None:
        try:
            if self._context is not None:
                await self._context.close()
        except Exception as exc:
            logger.warning("Error closing LinkedIn context: %s", exc)
        try:
            if self._pw is not None:
                await self._pw.stop()
        except Exception as exc:
            logger.warning("Error stopping LinkedIn Playwright: %s", exc)
        self._pw = self._browser = self._context = self._page = None
        self._account = None
        self._owner_email = None
        try:
            from worker.profile_lock import release_profile_lock

            release_profile_lock(self._profile_lock)
        finally:
            self._profile_lock = None

    async def start(self, owner_email: Optional[str] = None) -> dict:
        async with self._lifecycle_lock:
            if self.status == "running":
                if owner_email and not self.is_owned_by(owner_email):
                    return {
                        **self.snapshot(),
                        "status": "error",
                        "message": "A different LinkedIn account is already using live chat.",
                        "error": "Stop the existing live chat session before starting this account.",
                    }
                return self.snapshot()

            self.status = "starting"
            self.message = "Starting LinkedIn live chat…"
            self.error = None
            # Claim ownership before the first await so status/stop requests
            # from another authenticated account cannot observe or cancel this
            # account's in-progress launch.
            self._owner_email = owner_email

            try:
                async with async_session() as db:
                    query = select(LinkedInAccount).where(
                        LinkedInAccount.status.in_(
                            [LinkedInAccountStatus.ACTIVE, LinkedInAccountStatus.VALID]
                        )
                    )
                    if owner_email:
                        query = query.where(LinkedInAccount.owner_email == owner_email)
                    result = await db.execute(
                        query.order_by(desc(LinkedInAccount.updated_at)).limit(1)
                    )
                    self._account = result.scalars().first()

                if self._account is None:
                    raise RuntimeError(
                        "No active LinkedIn account found. Connect and verify LinkedIn first."
                    )

                from worker.profile_lock import acquire_profile_lock

                self._profile_lock = acquire_profile_lock(
                    self._account.id, blocking_timeout=0
                )
                self._pw, self._browser, self._context, self._page = (
                    await launch_persistent_browser(self._account, headless=True)
                )
                self._owner_email = self._account.owner_email
                await self._page.goto(
                    MESSAGING_URL, wait_until="domcontentloaded", timeout=60000
                )
                await self._page.wait_for_selector(
                    f"{CHAT_LIST_ROOT_SELECTOR}, {CHAT_ROW_PAGE_SELECTOR}", timeout=30000
                )

                self.status = "running"
                self.message = "LinkedIn live chat is running."
                return self.snapshot()
            except Exception as exc:
                logger.exception("Could not start LinkedIn live chat")
                await self._cleanup_resources()
                self.status = "error"
                self.error = str(exc)
                self.message = f"Could not start LinkedIn live chat: {exc}"
                return self.snapshot()

    async def stop(self) -> dict:
        async with self._lifecycle_lock:
            self.status = "stopping"
            self.message = "Stopping LinkedIn live chat…"
            async with self._operation_lock:
                await self._cleanup_resources()
                self.active_chat_id = None
                self.active_chat_name = None
                self.status = "idle"
                self.error = None
                self.message = "LinkedIn live chat stopped."
                return self.snapshot()

    async def _require_page(self):
        if self.status != "running" or self._page is None:
            raise RuntimeError("LinkedIn live chat is not running")
        if getattr(self._page, "is_closed", lambda: False)():
            raise RuntimeError("LinkedIn browser page is closed")
        return self._page

    async def _chat_rows(self, page: Any) -> list[Any]:
        root = await _first(page, CHAT_LIST_ROOT_SELECTOR)
        if root is not None:
            try:
                rows = await root.query_selector_all(CHAT_ROW_SELECTOR)
                if rows:
                    return rows
            except Exception:
                pass
        try:
            return await page.query_selector_all(CHAT_ROW_PAGE_SELECTOR)
        except Exception:
            return []

    async def _ensure_messaging(self, page: Any) -> None:
        if "/messaging" not in (getattr(page, "url", "") or ""):
            await page.goto(MESSAGING_URL, wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_selector(
                f"{CHAT_LIST_ROOT_SELECTOR}, {CHAT_ROW_PAGE_SELECTOR}", timeout=15000
            )
        except Exception:
            # A thread may hide the list on a narrow viewport; open/read still
            # have their own panel checks and should be allowed to continue.
            pass

    async def list_chats(self, limit: int = DEFAULT_CHAT_LIMIT) -> list[dict]:
        limit = max(1, min(int(limit or DEFAULT_CHAT_LIMIT), 100))
        async with self._operation_lock:
            page = await self._require_page()
            await self._ensure_messaging(page)
            rows = await self._chat_rows(page)
            chats: list[dict] = []
            seen: set[str] = set()
            for row in rows:
                try:
                    chat_id, name, preview, _href, unread = await _row_identity(row)
                    if chat_id in seen:
                        continue
                    seen.add(chat_id)
                    chats.append(
                        {
                            "chat_id": chat_id,
                            "name": name,
                            "preview": preview or None,
                            "unread_count": unread,
                        }
                    )
                    if len(chats) >= limit:
                        break
                except Exception as exc:
                    logger.debug("Could not parse LinkedIn conversation row: %s", exc)
            return chats

    async def open_chat(self, chat_id: str) -> dict:
        """Open an exactly-enumerated row; never interpolate input into CSS."""
        async with self._operation_lock:
            page = await self._require_page()
            await self._ensure_messaging(page)
            selected = None
            selected_name = None
            for row in await self._chat_rows(page):
                try:
                    row_id, name, _preview, _href, _unread = await _row_identity(row)
                except Exception:
                    continue
                if row_id == chat_id:
                    selected = row
                    selected_name = name
                    break

            if selected is None:
                return {
                    "ok": False,
                    "error": "That conversation is no longer visible. Refresh the list and try again.",
                }

            try:
                link = await _first(selected, CHAT_LINK_SELECTOR)
                await (link or selected).click()
                await page.wait_for_selector(THREAD_PANEL_SELECTOR, timeout=15000)
            except Exception as exc:
                logger.warning("Could not open LinkedIn conversation %s: %s", chat_id, exc)
                return {
                    "ok": False,
                    "error": "LinkedIn did not open that conversation. Refresh the list and try again.",
                }

            self.active_chat_id = chat_id
            self.active_chat_name = selected_name or chat_id
            return {
                "ok": True,
                "chat_id": chat_id,
                "name": self.active_chat_name,
            }

    async def close_active_chat(self) -> dict:
        async with self._operation_lock:
            await self._require_page()
            self.active_chat_id = None
            self.active_chat_name = None
            return {"ok": True, "chat_id": None, "name": None}

    async def read_messages(self, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[dict]:
        limit = max(1, min(int(limit or DEFAULT_MESSAGE_LIMIT), 200))
        async with self._operation_lock:
            page = await self._require_page()
            if not self.active_chat_id:
                return []
            try:
                await page.wait_for_selector(THREAD_PANEL_SELECTOR, timeout=10000)
            except Exception:
                raise RuntimeError(
                    "The selected LinkedIn conversation is no longer open. Open it again."
                )

            try:
                rows = await page.query_selector_all(MESSAGE_ROW_SELECTOR)
            except Exception:
                rows = []

            messages: list[dict] = []
            seen: set[str] = set()
            for index, row in enumerate(rows[-limit:]):
                text = await _short_text(await _first(row, MESSAGE_TEXT_SELECTOR), 4000)
                if not text:
                    continue
                sender = await _short_text(await _first(row, MESSAGE_SENDER_SELECTOR), 160)
                timestamp = await _short_text(await _first(row, MESSAGE_TIME_SELECTOR), 80)
                classes = (await _attribute(row, "class")).lower()
                from_me = (await _attribute(row, "data-from-me")).lower()
                is_outgoing = (
                    "from-me" in classes
                    or "--me" in classes
                    or "outgoing" in classes
                    or from_me in {"true", "1"}
                    or sender.lower() in {"you", "me"}
                )
                message_id = ""
                for attr_name in ("data-event-urn", "data-message-id", "data-urn", "id"):
                    message_id = await _attribute(row, attr_name)
                    if message_id:
                        break
                if not message_id:
                    digest = hashlib.sha256(
                        f"{sender}\n{text}\n{timestamp}\n{index}".encode("utf-8")
                    ).hexdigest()[:20]
                    message_id = f"dom-{digest}"
                if message_id in seen:
                    continue
                seen.add(message_id)
                messages.append(
                    {
                        "message_id": message_id,
                        "text": text,
                        "sender": sender or None,
                        "is_outgoing": is_outgoing,
                        "timestamp": timestamp or None,
                        "type": "text",
                    }
                )
            return messages

    async def send_message(self, text: str) -> dict:
        clean = (text or "").strip()
        if not clean:
            return {"ok": False, "error": "Message cannot be empty."}

        async with self._operation_lock:
            page = await self._require_page()
            if not self.active_chat_id:
                return {"ok": False, "error": "Open a chat before sending."}

            elapsed = time.monotonic() - self._last_send_monotonic
            wait_for = max(0.0, LIVE_SEND_DELAY_SECONDS - elapsed)
            if wait_for:
                await asyncio.sleep(wait_for)

            try:
                composer = page.locator(COMPOSER_SELECTOR).first
                await composer.wait_for(state="visible", timeout=10000)
                await composer.fill(clean)
                button = page.locator(SEND_BUTTON_SELECTOR).first
                try:
                    await button.click(timeout=5000)
                except Exception:
                    await composer.press("Enter")
                self._last_send_monotonic = time.monotonic()
                return {"ok": True, "throttled_seconds": round(wait_for, 2)}
            except Exception as exc:
                logger.warning("Could not send LinkedIn live message: %s", exc)
                return {
                    "ok": False,
                    "error": "LinkedIn's message composer was not available. Reopen the chat and try again.",
                }

    @asynccontextmanager
    async def profile_page(self):
        """Yield the shared page exclusively to the profile scanner."""
        async with self._operation_lock:
            page = await self._require_page()
            yield page


linkedin_live_browser = LinkedInLiveBrowserManager()

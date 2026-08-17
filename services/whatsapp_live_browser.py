"""
WhatsApp live-chat browser manager.

FILE: services/whatsapp_live_browser.py

A second persistent Playwright browser dedicated to the "live chat" UI.
Opens a chat list, lets the user click a chat and read/write messages
through the DOM — Read and Write go through the same Playwright page as
the existing scanner, but in a dedicated browser/process.

Sub-physics:
  * Chromium allows only ONE browser per user-data-dir, so we acquire the
    ``profile_lock:whatsapp`` Redis lock (same one the scanner task uses).
    While the live-chat browser is open the periodic scan task hits
    ProfileInUseError and skips with "⚠ WhatsApp profile in use" — exactly
    what we want.
  * Each manual ``send_message`` call also waits
    ``WHATSAPP_FORWARD_DELAY_SECONDS`` between sends so a fast-typing user
    does not trip WhatsApp's spam/blocking filter (the same fix that
    already paces the scanner's forward loop).

The frontend talks to this module via ``api/v1/whatsapp_live.py`` and polls
``/live/chats`` + ``/live/chats/{id}/messages`` every 3 seconds.
"""
import asyncio
import time
from typing import Optional

from core.config import settings
from core.logging_config import get_logger
from services.whatsapp_browser import (
    CHAT_LIST_SELECTOR,
    CHAT_NAME_SELECTOR,
    CHAT_ROW_SELECTOR,
    LAUNCH_ARGS,
    MSG_INPUT_SELECTOR,
    MSG_TEXT_SELECTOR,
    PANE_SIDE_SELECTOR,
    SEND_BUTTON_SELECTOR,
    STEALTH_SCRIPT,
    USER_AGENT,
    ensure_whatsapp_profile_dir,
    is_logged_in,
    navigate_to_whatsapp,
    safe_close,
    scrape_messages_from_current_chat,
)
from patchright.async_api import async_playwright

logger = get_logger(__name__)

# Seconds to wait between consecutive manual sends. Sourced from the same
# env var that paces the scanner's forward loop so operators have a single
# knob (WHATSAPP_FORWARD_DELAY_SECONDS).
LIVE_SEND_DELAY_SECONDS: float = float(
    getattr(settings, "WHATSAPP_FORWARD_DELAY_SECONDS", None) or 10.0
)

# Cap on how many chats / messages we expose per request. The chat list
# DOM is virtualized, so this only returns what's already visible — the
# frontend can scroll/filter via WhatsApp's own search box.
DEFAULT_CHAT_LIMIT = 50
DEFAULT_MESSAGE_LIMIT = 50

VIEWPORT = {"width": 1280, "height": 900}


class LiveBrowserManager:
    """Process-wide singleton managing the live-chat Playwright browser."""

    def __init__(self) -> None:
        self._pw = None
        self._context = None
        self._page = None
        self._profile_lock = None

        # Internal state exposed by ``snapshot()``.
        self.status = "idle"  # idle | starting | running | error | paused_by_scanner
        self.status_message = ""
        self.last_error: Optional[str] = None

        # Currently open chat (UI selection).
        self.active_chat_id: Optional[str] = None
        self.active_chat_name: Optional[str] = None

        # Anti-block pacing for manual sends.
        self._last_send_ts: float = 0.0
        self._send_lock = asyncio.Lock()

        self._op_lock = asyncio.Lock()

    # ── Read-only accessors ────────────────────────────────────────────────

    @property
    def page(self):
        return self._page

    def snapshot(self) -> dict:
        return {
            "status": self.status,
            "message": self.status_message,
            "error": self.last_error,
            "active_chat_id": self.active_chat_id,
            "active_chat_name": self.active_chat_name,
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> dict:
        """Launch the live browser on the durable WhatsApp profile.

        Acquires ``profile_lock:whatsapp`` so the Celery scan task pauses
        for the duration of this session. If WhatsApp is not connected, or
        if the lock is already held by someone else, returns immediately
        with a clear error.
        """
        async with self._op_lock:
            if self._page is not None and self.status in ("running", "starting"):
                return self.snapshot()

            # Verify the user has a connected WhatsApp session first —
            # there's nothing for the live browser to read otherwise.
            from worker.profile_lock import ProfileInUseError, acquire_profile_lock

            try:
                profile_lock = acquire_profile_lock("whatsapp", blocking_timeout=2)
            except ProfileInUseError:
                # Should not happen often — we hold the only locks — but make
                # the error user-readable, not a traceback.
                self._set_status(
                    "error",
                    "The WhatsApp browser is busy with another operation. "
                    "Try again in a few seconds.",
                )
                return self.snapshot()

            self._set_status("starting", "Launching WhatsApp live-chat browser…")

            try:
                pw = await async_playwright().start()
                try:
                    context = await pw.chromium.launch_persistent_context(
                        user_data_dir=ensure_whatsapp_profile_dir(),
                        headless=True,
                        viewport=dict(VIEWPORT),
                        locale="en-US",
                        timezone_id="America/New_York",
                        permissions=["notifications"],
                        user_agent=USER_AGENT,
                        args=LAUNCH_ARGS,
                    )
                except Exception:
                    try:
                        await pw.stop()
                    except Exception:
                        pass
                    raise

                page = context.pages[0] if context.pages else await context.new_page()
                for extra in [p for p in context.pages if p is not page]:
                    try:
                        await extra.close()
                    except Exception:
                        pass

                await context.add_init_script(STEALTH_SCRIPT)
                await navigate_to_whatsapp(page)

                # Best-effort login confirmation. If the profile directory was
                # evicted or cookies got wiped, ``is_logged_in`` will return
                # False and we surface a helpful error.
                for _ in range(20):
                    if await is_logged_in(page):
                        break
                    await asyncio.sleep(0.5)
                else:
                    raise RuntimeError(
                        "WhatsApp did not reach the chat list — the saved "
                        "session may have expired. Reconnect via the "
                        "WhatsApp Scanner connect flow."
                    )

                self._pw = pw
                self._context = context
                self._page = page
                self._profile_lock = profile_lock
                self.active_chat_id = None
                self.active_chat_name = None

            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("live chat browser failed to start")
                self._set_status("error", f"Failed to start live chat: {exc}")
                await self._shutdown_raw()
                return self.snapshot()

        self._set_status("running", "Live chat is open. The scanner is paused.")
        logger.info("✅ WhatsApp live-chat browser started (profile lock held)")
        return self.snapshot()

    async def stop(self) -> dict:
        """Stop the live browser and release the profile lock."""
        async with self._op_lock:
            await self._shutdown_raw()
        self.last_error = None
        self._set_status("idle", "Live chat closed.")
        return self.snapshot()

    async def _shutdown_raw(self) -> None:
        """Tear down browser resources. Caller holds ``self._op_lock``."""
        if self._pw is not None or self._context is not None:
            try:
                await safe_close(self._pw, self._context)
            except Exception:
                pass
        self._pw = None
        self._context = None
        self._page = None

        if self._profile_lock is not None:
            try:
                from worker.profile_lock import release_profile_lock

                release_profile_lock(self._profile_lock)
            except Exception:
                pass
            self._profile_lock = None

    # ── Chat list ──────────────────────────────────────────────────────────

    async def list_chats(self, filter_text: Optional[str] = None, limit: int = DEFAULT_CHAT_LIMIT) -> list[dict]:
        """Return a list of chats from the sidebar (groups + contacts).

        Optional ``filter_text`` performs an in-page search using WhatsApp's
        own search box so the result rows come back filtered server-side.
        """
        page = await self._require_page()
        # If the user previously opened a different chat, back out so the
        # whole sidebar is visible — WhatsApp hides the chat list while a
        # conversation is open on small screens.
        await self._ensure_chat_list_visible(page)

        if filter_text:
            search_box = page.locator(
                'div[contenteditable="true"][data-tab], input[placeholder*="Search"]'
            )
            try:
                await search_box.first.fill(filter_text, timeout=4000)
                await asyncio.sleep(1.5)
            except Exception as exc:
                logger.warning("Could not use WhatsApp search box: %s", exc)

        try:
            await page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=10000)
        except Exception:
            logger.warning("Chat list not visible — is WhatsApp logged in?")
            return []

        rows = await page.query_selector_all(CHAT_ROW_SELECTOR)
        chats: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            try:
                name_el = await row.query_selector(CHAT_NAME_SELECTOR)
                if name_el is None:
                    spans = await row.query_selector_all("span[dir='auto']")
                    name_el = spans[0] if spans else None
                if name_el is None:
                    continue
                name = (await name_el.inner_text()).strip()
                if not name:
                    continue

                chat_id = (
                    await row.get_attribute("data-id")
                    or await row.get_attribute("aria-label")
                    or name
                )
                if chat_id in seen:
                    continue
                seen.add(chat_id)

                # Last message + unread count are opportunistic — the DOM
                # exposes them via the last-bubble selector and a numeric
                # badge span. Skip on failure rather than block the list.
                preview = await _read_last_preview(row)
                unread = await _read_unread_count(row)

                chats.append(
                    {
                        "chat_id": chat_id,
                        "name": name,
                        "preview": preview,
                        "unread_count": unread,
                    }
                )
                if len(chats) >= limit:
                    break
            except Exception:
                continue

        if filter_text:
            await _clear_search(box=page.locator(
                'div[contenteditable="true"][data-tab], input[placeholder*="Search"]'
            ).first)

        return chats

    async def open_chat(self, chat_id: str) -> dict:
        """Click a chat in the sidebar by ``chat_id`` (data-id/aria/name)."""
        page = await self._require_page()
        await self._ensure_chat_list_visible(page)

        # Clear any active search so the chat is findable.
        await _clear_search(
            box=page.locator(
                'div[contenteditable="true"][data-tab], input[placeholder*="Search"]'
            ).first
        )

        row = None
        try:
            row = await page.wait_for_selector(
                f'{CHAT_ROW_SELECTOR}[data-id="{chat_id}"], '
                f'{CHAT_ROW_SELECTOR}[aria-label="{chat_id}"]',
                timeout=4000,
            )
        except Exception:
            pass

        if row is None:
            # Fall back to scanning rows for a matching aria/data-id
            # combined with a name match, since some chat rows lack
            # ``data-id``.
            for r in await page.query_selector_all(CHAT_ROW_SELECTOR):
                nid = (
                    await r.get_attribute("data-id")
                    or await r.get_attribute("aria-label")
                    or ""
                )
                if nid == chat_id:
                    row = r
                    break
                name_el = await r.query_selector(CHAT_NAME_SELECTOR)
                if name_el is not None and (await name_el.inner_text()).strip() == chat_id:
                    row = r
                    break

        if row is None:
            return {"ok": False, "error": f"Chat '{chat_id}' not found"}

        try:
            await row.click()
        except Exception as exc:
            return {"ok": False, "error": f"Could not click chat: {exc}"}

        # Wait for the conversation panel to appear and the header text to
        # settle — header mirrors the chat name.
        try:
            await page.wait_for_selector(
                'div[data-testid="conversation-panel-wrapper"], div[data-testid="conversation-panel"]',
                timeout=8000,
            )
        except Exception:
            return {"ok": False, "error": "Chat did not open (panel not visible)"}

        # Best-effort resolve of the chat name from the conversation header.
        name = await _read_active_chat_name(page)
        self.active_chat_id = chat_id
        self.active_chat_name = name or chat_id
        return {"ok": True, "chat_id": chat_id, "name": self.active_chat_name}

    async def close_active_chat(self) -> dict:
        """Go back to the chat list so the sidebar is visible again."""
        page = await self._require_page()
        await self._ensure_chat_list_visible(page)
        return {"ok": True, "active_chat_id": None}

    async def read_messages(self, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[dict]:
        """Read the messages currently visible in the open chat."""
        page = await self._require_page()
        if not self.active_chat_id:
            return []

        # Reuse the scanner's read path but ignore its incremental cursor —
        # in live view we always re-snapshot what's on screen.
        raw = await scrape_messages_from_current_chat(
            page,
            last_message_id=None,
            last_timestamp=0,
            message_limit=limit,
        )

        out = []
        for msg in raw or []:
            out.append(
                {
                    "whatsapp_message_id": msg.get("whatsapp_message_id"),
                    "sender": msg.get("sender_name"),
                    "text": msg.get("message_text") or "",
                    "type": msg.get("message_type", "text"),
                    "is_outgoing": bool(msg.get("is_outgoing", False)),
                    "timestamp": msg.get("timestamp"),
                }
            )
        # Reverse so older messages come first (chat-style display).
        out.reverse()
        return out

    async def send_message(self, text: str) -> dict:
        """Type ``text`` into the input box and click send.

        Waits up to ``LIVE_SEND_DELAY_SECONDS`` between consecutive sends
        so a fast-typing user doesn't trip WhatsApp's blocking filter.
        """
        page = await self._require_page()
        if not self.active_chat_id:
            return {"ok": False, "error": "Open a chat first"}
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Empty message"}

        async with self._send_lock:
            now = time.monotonic()
            wait = LIVE_SEND_DELAY_SECONDS - (now - self._last_send_ts)
            if self._last_send_ts and wait > 0:
                logger.info(
                    "⏳ Throttling manual WhatsApp send by %.1fs "
                    "(anti-blocking filter)", wait,
                )
                await asyncio.sleep(wait)

            try:
                input_box = await page.query_selector(MSG_INPUT_SELECTOR)
                if input_box is None:
                    boxes = await page.query_selector_all(
                        'div[contenteditable="true"]'
                    )
                    for ib in boxes:
                        try:
                            tab = await ib.get_attribute("data-tab")
                            if tab == "10":
                                input_box = ib
                                break
                        except Exception:
                            continue
                if input_box is None:
                    return {"ok": False, "error": "Message input box not visible"}

                await input_box.click()
                await asyncio.sleep(0.3)

                # Paste via ClipboardEvent — same technique the scanner uses
                # to inject forwarded messages. This is what WhatsApp Web
                # expects, vs a plain ``.fill()`` which can lose formatting.
                await page.evaluate(
                    """(t) => {
                        const dt = new DataTransfer();
                        dt.setData('text/plain', t);
                        const ev = new ClipboardEvent('paste', {
                            clipboardData: dt, bubbles: true, cancelable: true,
                        });
                        document.activeElement.dispatchEvent(ev);
                    }""",
                    text,
                )
                await asyncio.sleep(0.4)

                send_btn = await page.query_selector(SEND_BUTTON_SELECTOR)
                if send_btn:
                    await send_btn.click()
                else:
                    await page.keyboard.press("Enter")

                await asyncio.sleep(0.6)
            except Exception as exc:
                return {"ok": False, "error": f"Send failed: {exc}"}

        self._last_send_ts = time.monotonic()
        return {"ok": True}

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _require_page(self):
        if self._page is None or self.status != "running":
            raise RuntimeError("Live chat is not running. Start it first.")
        return self._page

    async def _ensure_chat_list_visible(self, page) -> None:
        # If the pane-side (chat list sidebar) is not present, the
        # conversation view took over the screen. Use a back gesture to
        # return to the sidebar without losing the message we want.
        try:
            sidebar = await page.query_selector(PANE_SIDE_SELECTOR)
            if sidebar is None:
                # Try to click the back arrow or press Escape.
                back = await page.query_selector(
                    'div[data-testid="back"], [aria-label="Back"]'
                )
                if back is not None:
                    await back.click()
                else:
                    await page.keyboard.press("Escape")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    def _set_status(self, status: str, message: str) -> None:
        self.status = status
        self.status_message = message
        logger.info("📱 live chat status: %s — %s", status, message)


async def _read_last_preview(row) -> Optional[str]:
    """Best-effort last-message preview for a chat row."""
    for selector in (
        "span[data-testid='last-msg-status'] + div span[dir='auto']",
        "span[data-testid='last-msg-status']",
        "div[data-id] span[dir='ltr']",
        "span[dir='ltr']",
    ):
        try:
            el = await row.query_selector(selector)
        except Exception:
            el = None
        if el is None:
            continue
        try:
            txt = (await el.inner_text()).strip()
        except Exception:
            continue
        if txt:
            return txt[:120]
    return None


async def _read_unread_count(row) -> int:
    try:
        el = await row.query_selector(
            'span[data-testid="icon-unread-count"], '
            'span[aria-label*="unread" i]'
        )
    except Exception:
        el = None
    if el is None:
        return 0
    try:
        text = (await el.inner_text()).strip()
    except Exception:
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


async def _clear_search(box) -> None:
    if box is None:
        return
    try:
        await box.click(timeout=2000)
    except Exception:
        return
    try:
        await box.fill("")
        await asyncio.sleep(0.3)
    except Exception:
        try:
            await box.press("Escape")
        except Exception:
            pass


async def _read_active_chat_name(page) -> Optional[str]:
    for selector in (
        "header div[data-testid='conversation-header'] span[dir='auto']",
        "header span[dir='auto']",
        "div[data-testid='conversation-header'] span[dir='auto']",
    ):
        try:
            el = await page.query_selector(selector)
        except Exception:
            el = None
        if el is None:
            continue
        try:
            name = (await el.inner_text()).strip()
        except Exception:
            continue
        if name:
            return name[:200]
    return None


# Process-wide singleton — the FastAPI app owns one live browser at a time.
live_browser = LiveBrowserManager()

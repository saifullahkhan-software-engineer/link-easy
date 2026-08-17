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
    MAIN_PANE_SELECTOR,
    MSG_CONTAINER_FALLBACK_SELECTOR,
    MSG_CONTAINER_SELECTOR,
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

# Keep the default list deliberately small: WhatsApp's sidebar is virtualized,
# and the first ten rows are the user's most recent conversations. Older chats
# remain available through the search field.
DEFAULT_CHAT_LIMIT = 10
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
        # Every list/open/read/send operation shares one Playwright page. Keep
        # them serialized so sidebar polling cannot mutate the DOM while a
        # message snapshot or chat selection is in progress.
        self._page_lock = asyncio.Lock()

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

            # Store the lock immediately. Browser launch and login checks can
            # fail before ``self._page`` is assigned; keeping it only in a local
            # variable leaked profile_lock:whatsapp for its full 30-minute TTL
            # after a failed start and made every retry look broken.
            self._profile_lock = profile_lock
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

                # Register resources before any navigation/login work. If one
                # of those steps fails, ``_shutdown_raw`` must still be able to
                # close Chromium rather than leaving a SingletonLock behind.
                self._pw = pw
                self._context = context
                self._page = page

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

                self.active_chat_id = None
                self.active_chat_name = None
                # Publish running state while the lifecycle lock is still held.
                # Otherwise a concurrent stop can tear everything down between
                # lock release and this assignment, leaving status="running"
                # with no browser page.
                self._set_status("running", "Live chat is open. The scanner is paused.")

            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("live chat browser failed to start")
                self._set_status("error", f"Failed to start live chat: {exc}")
                await self._shutdown_raw()
                return self.snapshot()

            logger.info("✅ WhatsApp live-chat browser started (profile lock held)")
            return self.snapshot()

    async def stop(self) -> dict:
        """Stop the live browser and release the profile lock."""
        async with self._op_lock:
            async with self._page_lock:
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
        self.active_chat_id = None
        self.active_chat_name = None
        self._last_send_ts = 0.0

        if self._profile_lock is not None:
            try:
                from worker.profile_lock import release_profile_lock

                release_profile_lock(self._profile_lock)
            except Exception:
                pass
            self._profile_lock = None

    # ── Chat list ──────────────────────────────────────────────────────────

    async def list_chats(
        self,
        filter_text: Optional[str] = None,
        limit: int = DEFAULT_CHAT_LIMIT,
    ) -> list[dict]:
        """Return the most recent chats from WhatsApp's sidebar.

        Sidebar polling and chat selection share a lock because both operations
        touch WhatsApp's virtualized chat rows and search box.
        """
        async with self._page_lock:
            page = await self._require_page()
            await self._ensure_chat_list_visible(page)

            search_box = _chat_search_box(page)
            if filter_text:
                try:
                    await search_box.fill(filter_text, timeout=4000)
                    await asyncio.sleep(1.0)
                except Exception as exc:
                    logger.warning("Could not use WhatsApp search box: %s", exc)
            else:
                # Clearing the app's filter must also clear WhatsApp's own
                # search state. Keep a non-empty search in place so a result
                # remains clickable after it has been returned to the client.
                await _clear_search(search_box)

            try:
                await page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=10000)
            except Exception:
                logger.warning("Chat list not visible — is WhatsApp logged in?")
                return []

            rows = await _chat_rows(page)
            chats: list[dict] = []
            seen: set[str] = set()
            for row in rows:
                try:
                    chat_id, name = await _chat_row_identity(row)
                    if not chat_id or not name or chat_id in seen:
                        continue
                    seen.add(chat_id)

                    chats.append(
                        {
                            "chat_id": chat_id,
                            "name": name,
                            "preview": await _read_last_preview(row),
                            "unread_count": await _read_unread_count(row),
                        }
                    )
                    if len(chats) >= limit:
                        break
                except Exception:
                    # WhatsApp can detach virtualized rows while they are read.
                    continue

            return chats

    async def open_chat(self, chat_id: str) -> dict:
        """Click a currently visible chat by stable id or displayed name."""
        async with self._page_lock:
            page = await self._require_page()
            await self._ensure_chat_list_visible(page)

            # Do not clear the search here: a filtered result may be an older
            # conversation that disappears from the virtualized top-ten list
            # as soon as search is cleared.
            row = None
            selected_name = None
            for candidate in await _chat_rows(page):
                try:
                    candidate_id, candidate_name = await _chat_row_identity(candidate)
                except Exception:
                    continue
                if candidate_id == chat_id or candidate_name == chat_id:
                    row = candidate
                    selected_name = candidate_name
                    break

            if row is None:
                return {"ok": False, "error": f"Chat '{chat_id}' is no longer visible. Refresh the chat list and try again."}

            try:
                await row.click()
                # ``#main`` is the stable selector in current WhatsApp Web;
                # data-testid values are retained as legacy fallbacks.
                await page.wait_for_selector(MAIN_PANE_SELECTOR, timeout=8000)
            except Exception as exc:
                logger.warning("Could not open WhatsApp chat %s: %s", chat_id, exc)
                return {"ok": False, "error": "Chat did not open. Refresh the list and try again."}

            name = await _read_active_chat_name(page)
            self.active_chat_id = chat_id
            self.active_chat_name = name or selected_name or chat_id
            return {"ok": True, "chat_id": chat_id, "name": self.active_chat_name}

    async def close_active_chat(self) -> dict:
        """Clear the selected conversation and return to the list on mobile."""
        async with self._page_lock:
            page = await self._require_page()
            await self._ensure_chat_list_visible(page)
            self.active_chat_id = None
            self.active_chat_name = None
            return {"ok": True, "chat_id": None, "name": None}

    async def read_messages(self, limit: int = DEFAULT_MESSAGE_LIMIT) -> list[dict]:
        """Read the newest messages in the open chat.

        The shared scanner walks upward through WhatsApp's virtualized history.
        A live-chat poll must therefore start at the bottom and restore the
        bottom afterwards; otherwise each poll begins where the previous poll
        stopped and eventually returns only increasingly old messages.
        """
        async with self._page_lock:
            page = await self._require_page()
            if not self.active_chat_id:
                return []

            if await _scroll_message_history_to_bottom(page):
                await asyncio.sleep(0.4)

            # Reuse the scanner's read path but ignore its incremental cursor —
            # in live view we always re-snapshot what's on screen. The scraper
            # returns newest-first after walking upward through bounded history.
            try:
                raw = await scrape_messages_from_current_chat(
                    page,
                    last_message_id=None,
                    last_timestamp=None,
                    message_limit=limit,
                )
            finally:
                # Keep the next poll anchored to the newest conversation
                # window even when extraction raises midway through a read.
                if await _scroll_message_history_to_bottom(page):
                    await asyncio.sleep(0.4)

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
            # The scanner walks newest-to-oldest; chat UI needs oldest-to-newest.
            out.reverse()
            return out

    async def send_message(self, text: str) -> dict:
        """Type ``text`` into the active composer with anti-block pacing."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "Empty message"}

        async with self._send_lock:
            now = time.monotonic()
            wait = LIVE_SEND_DELAY_SECONDS - (now - self._last_send_ts)
            if self._last_send_ts and wait > 0:
                logger.info(
                    "⏳ Throttling manual WhatsApp send by %.1fs "
                    "(anti-blocking filter)",
                    wait,
                )
                await asyncio.sleep(wait)

            async with self._page_lock:
                page = await self._require_page()
                if not self.active_chat_id:
                    return {"ok": False, "error": "Open a chat first"}

                try:
                    input_box = await page.query_selector(MSG_INPUT_SELECTOR)
                    if input_box is None:
                        boxes = await page.query_selector_all(
                            '#main div[contenteditable="true"], '
                            'footer div[contenteditable="true"]'
                        )
                        for candidate in boxes:
                            try:
                                if await candidate.get_attribute("data-tab") == "10":
                                    input_box = candidate
                                    break
                            except Exception:
                                continue
                    if input_box is None:
                        return {
                            "ok": False,
                            "error": "Message input box not visible",
                        }

                    await input_box.click()
                    await asyncio.sleep(0.3)
                    # Paste via ClipboardEvent — WhatsApp's contenteditable
                    # composer handles this more reliably than ``fill()``.
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


async def _scroll_message_history_to_bottom(page) -> bool:
    """Move WhatsApp's virtualized message pane to its newest window.

    Returns whether the pane moved. All failures are best-effort because a
    newly opened or very short conversation can already be at the bottom and
    some WhatsApp builds briefly replace the scroll container while rendering.
    """
    try:
        result = await page.evaluate(
            """async (selector) => {
                const messages = Array.from(document.querySelectorAll(selector));
                const last = messages[messages.length - 1];
                if (!last) return { moved: false };

                let scrollable = null;
                for (let node = last; node && node !== document.body; node = node.parentElement) {
                    const style = window.getComputedStyle(node);
                    const canScroll = node.scrollHeight > node.clientHeight + 4;
                    const scrollStyle = style.overflowY === 'auto' || style.overflowY === 'scroll';
                    if (canScroll && scrollStyle) {
                        scrollable = node;
                        break;
                    }
                }
                if (!scrollable) return { moved: false };

                const before = scrollable.scrollTop;
                scrollable.scrollTop = scrollable.scrollHeight;
                await new Promise(resolve => requestAnimationFrame(resolve));
                return { moved: scrollable.scrollTop > before + 1 };
            }""",
            f"{MSG_CONTAINER_SELECTOR}, {MSG_CONTAINER_FALLBACK_SELECTOR}",
        )
        return bool(isinstance(result, dict) and result.get("moved"))
    except Exception as exc:
        logger.debug("Could not restore WhatsApp live chat to the bottom: %s", exc)
        return False


def _chat_search_box(page):
    """Return the WhatsApp chat-search input, never the message composer."""
    return page.locator(
        '#side div[contenteditable="true"][role="textbox"], '
        'div[data-testid="chat-list-search"], '
        'div[contenteditable="true"][data-tab="3"], '
        'div[contenteditable="true"][aria-placeholder*="Search" i], '
        'input[placeholder*="Search" i]'
    ).first


async def _chat_rows(page) -> list:
    """Read only sidebar rows so message rows are never mistaken for chats."""
    try:
        sidebar = await page.query_selector(PANE_SIDE_SELECTOR)
    except Exception:
        sidebar = None
    if sidebar is None:
        return []
    return await sidebar.query_selector_all(CHAT_ROW_SELECTOR)


async def _chat_row_identity(row) -> tuple[Optional[str], Optional[str]]:
    """Return the same stable identity for listing and later row selection."""
    name_el = await row.query_selector(CHAT_NAME_SELECTOR)
    if name_el is None:
        spans = await row.query_selector_all("span[dir='auto']")
        name_el = spans[0] if spans else None
    if name_el is None:
        return None, None

    name = (await name_el.inner_text()).strip()
    if not name:
        return None, None

    chat_id = (
        await row.get_attribute("data-id")
        or await row.get_attribute("aria-label")
        or name
    )
    return str(chat_id).strip(), name


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

"""
LinkedIn live-chat browser manager.

FILE: services/linkedin_live_browser.py

Mirrors ``services/whatsapp_live_browser.py`` but for LinkedIn messaging.
Acquires ``profile_lock("linkedin")`` so the periodic scan task pauses
while a user is chatting; the underlying Playwright context is opened on
the same user-data-dir the user's ``LinkedInAccount`` row already keeps
logged-in on, so no fresh login is required.
"""
import asyncio
import time
from typing import Optional

from core.config import settings
from core.logging_config import get_logger
from patchright.async_api import async_playwright

# Re-use the existing LinkedIn persistent launcher — uses the pinned
# fingerprint on the linked account, same proxy, same user-data-dir.
from automation.browser import launch_persistent_browser
from services.whatsapp_browser import (
    LAUNCH_ARGS,  # noqa: F401  (kept for parity with the WhatsApp live module)
)

# We accept a couple of selectors overlapping with whatsapp_browser for
# readability — these are deliberately scoped to LinkedIn's messaging UI.
LINKEDIN_MESSAGING_URL = "https://www.linkedin.com/messaging/"

LIVE_SEND_DELAY_SECONDS: float = float(
    getattr(settings, "WHATSAPP_FORWARD_DELAY_SECONDS", None) or 10.0
)

DEFAULT_CHATS_LIMIT: int = 30

logger = get_logger(__name__)


def _short_text(el, *, max_len: int = 120) -> str:
    """Read ``textContent`` from an async element without raising."""
    if el is None:
        return ""
    try:
        return (el.inner_text() or "").strip()[:max_len]
    except Exception:
        return ""


class LinkedInLiveBrowser:
    """Process-wide singleton managing the LinkedIn live-chat browser."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None  # launch_persistent_browser returns (pw, browser=None, ...)
        self._context = None
        self._page = None
        self._profile_lock = None
        self._account = None  # type: ignore[assignment]

        self.status = "idle"  # idle | starting | running | error
        self.status_message = ""
        self.last_error: Optional[str] = None
        self.active_chat_id: Optional[str] = None
        self.active_chat_name: Optional[str] = None

        self._last_send_ts: float = 0.0
        self._send_lock = asyncio.Lock()
        self._op_lock = asyncio.Lock()

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
        """Open the LinkedIn live browser on the user's connected account."""
        async with self._op_lock:
            if self._page is not None and self.status in ("running", "starting"):
                return self.snapshot()

            from api.dependencies import get_db  # local to avoid heavy import at module load
            from models.linkedin_account import LinkedInAccount
            from worker.profile_lock import ProfileInUseError, acquire_profile_lock

            # Find the user's most-recent connected LinkedIn account.
            acct = None
            sync_sess = None
            try:
                import asyncio as _asyncio
                from database import async_session as _async_session
                from sqlalchemy.future import select as _select

                async with _async_session() as s:
                    row = await s.execute(
                        _select(LinkedInAccount).order_by(LinkedInAccount.id.desc()).limit(1)
                    )
                    acct = row.scalars().first()
                if acct is None:
                    self._set_status(
                        "error",
                        "No connected LinkedIn account found. Connect one in Account first.",
                    )
                    return self.snapshot()
                # Snapshot the fields we need — the live session must not
                # race with the scan task mutating the row.
                self._account = self._snapshot_account(acct)
            except Exception as exc:  # pragma: no cover — DB lookup failure
                self._set_status("error", f"Could not load LinkedIn account: {exc}")
                return self.snapshot()
            finally:
                # sync_sess intentionally unused — async session above is auto-closed.
                pass

            try:
                profile_lock = acquire_profile_lock("linkedin", blocking_timeout=2)
            except ProfileInUseError:
                self._set_status(
                    "error",
                    "The LinkedIn browser is busy with another operation.",
                )
                return self.snapshot()

            self._set_status("starting", "Launching LinkedIn live browser…")
            try:
                pw, _, context, page = await launch_persistent_browser(self._account, headless=True)
            except Exception as exc:
                self._set_status("error", f"Could not open LinkedIn browser: {exc}")
                try:
                    from worker.profile_lock import release_profile_lock
                    release_profile_lock(profile_lock)
                except Exception:
                    pass
                return self.snapshot()

            # Warm-up: navigate to messaging and wait for the conversation list.
            try:
                await page.goto(LINKEDIN_MESSAGING_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                # Soft-warn — some org/region pages block networkidle. Keep going.
                pass

            self._pw = pw
            self._context = context
            self._page = page
            self._profile_lock = profile_lock

        self._set_status("running", "LinkedIn live chat is open. The scanner paused.")
        return self.snapshot()

    async def stop(self) -> dict:
        async with self._op_lock:
            await self._shutdown_raw()
        self._set_status("idle", "LinkedIn live chat closed.")
        return self.snapshot()

    async def _shutdown_raw(self) -> None:
        if self._pw is not None or self._context is not None:
            try:
                if self._context is not None:
                    await self._context.close()
                if self._pw is not None:
                    await self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._account = None

        if self._profile_lock is not None:
            try:
                from worker.profile_lock import release_profile_lock
                release_profile_lock(self._profile_lock)
            except Exception:
                pass
            self._profile_lock = None

    # ── Chats ──────────────────────────────────────────────────────────────

    async def _require_page(self):
        if self._page is None or self.status != "running":
            raise RuntimeError("LinkedIn live chat is not running. Start it first.")
        return self._page

    async def list_chats(self, limit: int = 30) -> list[dict]:
        page = await self._require_page()
        # LinkedIn's messaging conversation list lives inside the messaging
        # thread-list container. Items are anchor links to /messaging/thread/<id>.
        try:
            await page.wait_for_selector(
                "a[href*='/messaging/thread/'], a[href*='messaging-thread']",
                timeout=10000,
            )
        except Exception:
            return []

        items = await page.query_selector_all("a[href*='/messaging/thread/']")
        chats: list[dict] = []
        seen: set[str] = set()
        for item in items:
            try:
                href = (await item.get_attribute("href")) or ""
                if not href:
                    continue
                # href is usually "/messaging/thread/ACRO-1234-NAME/?..."
                marker = "/thread/"
                pos = href.find(marker)
                if pos == -1:
                    continue
                tail = href[pos + len(marker):]
                chat_id = tail.split("?")[0].split("/")[0]
                if not chat_id or chat_id in seen:
                    continue
                seen.add(chat_id)

                name_el = await item.query_selector(
                    "span.msg-thread__title, span[class*='thread-name'], .msg-thread__top-row span"
                )
                preview_el = await item.query_selector(
                    ".msg-thread__last-message, .thread-preview, blockquote"
                )
                unread_el = await item.query_selector(".notification-badge, .badge, [aria-label*='unread']")
                chats.append(
                    {
                        "chat_id": chat_id,
                        "name": _short_text(name_el) or chat_id,
                        "preview": _short_text(preview_el, max_len=200),
                        "unread_count": _read_unread(unread_el),
                    }
                )
                if len(chats) >= limit:
                    break
            except Exception:
                continue
        return chats

    async def open_chat(self, chat_id: str) -> dict:
        page = await self._require_page()
        for_href = f"/messaging/thread/{chat_id}"
        link = await page.query_selector(f"a[href*='{for_href}']")
        if link is None:
            for a in await page.query_selector_all("a[href*='/messaging/thread/']"):
                href = (await a.get_attribute("href")) or ""
                if chat_id in href:
                    link = a
                    break
        if link is None:
            return {"ok": False, "error": f"Chat '{chat_id}' not found"}

        try:
            await link.click()
        except Exception as exc:
            return {"ok": False, "error": f"Could not click chat: {exc}"}

        try:
            await page.wait_for_selector(
                "div.msg-conversation-card, div.msg-feed, [data-conversation-id]",
                timeout=8000,
            )
        except Exception:
            return {"ok": False, "error": "Chat opened but conversation panel is empty"}

        # Best-effort title lookup.
        name_el = await page.query_selector(
            "header.msg-thread__topbar h2, h2.msg-thread__title, header h2"
        )
        name = _short_text(name_el) or chat_id
        self.active_chat_id = chat_id
        self.active_chat_name = name
        return {"ok": True, "chat_id": chat_id, "name": name}

    async def close_active_chat(self) -> dict:
        if self._page is None:
            return {"ok": True, "active_chat_id": None}
        # Navigate back to the messaging thread list — pressing the back
        # arrow on the conversation header or simply going to /messaging/.
        try:
            await self._require_page().goto(LINKEDIN_MESSAGING_URL, wait_until="domcontentloaded")
        except Exception:
            pass
        self.active_chat_id = None
        self.active_chat_name = None
        return {"ok": True, "active_chat_id": None}

    async def read_messages(self, limit: int = 50) -> list[dict]:
        page = await self._require_page()
        if not self.active_chat_id:
            return []
        try:
            await page.wait_for_selector(
                ".msg-s-message-list, ul.msg-s-message-list, div.msg-feed__message-list",
                timeout=10000,
            )
        except Exception:
            return []

        bubbles = await page.query_selector_all(
            ".msg-s-message-list li, .msg-feed__message-list li, li.msg-s-message-list__item"
        )
        out: list[dict] = []
        for b in bubbles:
            try:
                text_el = await b.query_selector(
                    "p.msg-s-message-list__text, .msg-s-message-list__content p, .msg-s-message-listitem__text"
                )
                # LinkedIn marks own messages with the `[data-sending-status]`
                # attribute or `.msg-s-message-listitem--me` class.
                cls = (await b.get_attribute("class")) or ""
                is_outgoing = "listitem--me" in cls or "outgoing" in cls
                sender_el = await b.query_selector(
                    ".msg-s-message-group__name, .msg-s-message-listitem__sender, h3.msg-s-message-group__name"
                )
                out.append(
                    {
                        "whatsapp_message_id": None,  # LinkedIn doesn't expose a stable id DOM-side
                        "sender": _short_text(sender_el),
                        "text": _short_text(text_el, max_len=2000),
                        "type": "text",
                        "is_outgoing": bool(is_outgoing),
                        "timestamp": None,
                    }
                )
                if len(out) >= limit:
                    break
            except Exception:
                continue
        return out

    async def send_message(self, text: str) -> dict:
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
                    "⏳ Throttling manual LinkedIn send by %.1fs (anti-blocking filter)",
                    wait,
                )
                await asyncio.sleep(wait)

            # LinkedIn's composer is a contenteditable div with role="textbox".
            editor = await page.query_selector(
                "div.msg-form__contenteditable[role='textbox'], div.msg-s-message-form__contenteditable, "
                "div[role='textbox'][contenteditable='true']"
            )
            if editor is None:
                return {"ok": False, "error": "Composer not visible"}
            try:
                await editor.click()
                await asyncio.sleep(0.2)
                # Match the same paste-first technique used by the WhatsApp
                # live browser — a plain `.fill()` would bypass the messaging
                # UI's draft logic.
                await page.evaluate(
                    """t => {
                        const dt = new DataTransfer();
                        dt.setData('text/plain', t);
                        const ev = new ClipboardEvent('paste', {
                            clipboardData: dt, bubbles: true, cancelable: true,
                        });
                        const el = document.activeElement || document.querySelector(
                            "div[role='textbox'][contenteditable='true']"
                        );
                        if (el) el.dispatchEvent(ev);
                    }""",
                    text,
                )
                await asyncio.sleep(0.4)
            except Exception as exc:
                return {"ok": False, "error": f"Could not type into composer: {exc}"}

            # LinkedIn sends on Enter (or shift+Enter for newline). The
            # easiest reliable send is the dedicated "Send" button — fall
            # back to keyboard if not present.
            try:
                send_btn = await page.query_selector(
                    "button.msg-form__send-button, button[type='submit'][data-control-name='send']"
                )
                if send_btn:
                    await send_btn.click()
                else:
                    await editor.press("Enter")
                await asyncio.sleep(0.6)
            except Exception as exc:
                return {"ok": False, "error": f"Send click failed: {exc}"}

        self._last_send_ts = time.monotonic()
        return {"ok": True}

    # ── Helpers ───────────────────────────────────────────────────────────

    def _set_status(self, status: str, message: str) -> None:
        self.status = status
        self.status_message = message
        logger.info("📡 LinkedIn live status: %s — %s", status, message)

    @staticmethod
    def _snapshot_account(acct) -> "SimpleNamespace":
        from types import SimpleNamespace
        return SimpleNamespace(
            id=acct.id,
            profile_dir=acct.profile_dir,
            user_agent=getattr(acct, "user_agent", None),
            viewport_width=getattr(acct, "viewport_width", 1440),
            viewport_height=getattr(acct, "viewport_height", 900),
            timezone_id=getattr(acct, "timezone_id", "America/Los_Angeles"),
            locale=getattr(acct, "locale", "en-US"),
            hardware_concurrency=getattr(acct, "hardware_concurrency", 8),
            device_memory=getattr(acct, "device_memory", 8),
            proxy_host=getattr(acct, "proxy_host", None),
            proxy_port=getattr(acct, "proxy_port", None),
            proxy_username=getattr(acct, "proxy_username", None),
            proxy_password_enc=getattr(acct, "proxy_password_enc", None),
        )


def _read_unread(el) -> int:
    if el is None:
        return 0
    try:
        text = (_short_text(el) or "").strip()
    except Exception:
        return 0
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else 0


# Process-wide singleton — the FastAPI app owns one LinkedIn live browser.
linkedin_live_browser = LinkedInLiveBrowser()

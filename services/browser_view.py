"""
In-process Playwright "browser view" manager.

FILE: services/browser_view.py

Runs a headless Chromium (via patchright, a Playwright fork) inside the
FastAPI process and streams the live screen to connected clients as JPEG
frames over the ``/api/v1/live/browser/stream`` SSE endpoint.  Clients can
also dispatch input events (click / scroll / type / key / navigate) so the
browser behaves like an embedded remote-control view.

Why this exists
---------------
The old WhatsApp connect flow launched a NON-headless browser with
``headless=False`` in the Celery worker.  On a server / container / sandbox
there is no display, so the "browser window" that the frontend promised never
appeared anywhere the user could see it — the page just sat there saying
"A browser window has opened...". 

With this manager the QR code is rendered into the WhatsApp Scanner page
itself: the browser runs headless, `Page.startScreencast` (CDP) emits JPEG
frames, and the frontend displays them as a live view the user can click
and scroll.  A screenshot-polling fallback is used if screencast is
unsupported in the bundled driver.

Note: The browser is ONLY opened for QR scan and 2FA entry. After successful
connection, the browser is stopped to free resources. Logs go to terminal.
"""
import asyncio
import base64
import logging
import time
from typing import Optional

from core.live_hub import EventHub

logger = logging.getLogger("browser_view")

WHATSAPP_URL = "https://web.whatsapp.com/"

# Browser viewport — input coordinates are normalized against these.
VIEWPORT = {"width": 1280, "height": 900}

# Screencast encoding — kept small so SSE frames stay cheap.
SCREENCAST_PARAMS = {
    "format": "jpeg",
    "quality": 42,
    "maxWidth": 800,
    "maxHeight": 450,
    "everyNthFrame": 5,
}

# Minimum gap between published frames (~2 fps max) — the screencast can emit
# frames far faster than a web page needs.
MIN_FRAME_INTERVAL = 0.5

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--no-first-run",
    "--disable-gpu",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
"""


class BrowserViewManager:
    """Singleton-ish manager for the embedded, streamable browser."""

    def __init__(self) -> None:
        # Frame/status event hub (no history — frames are big).
        self.events: EventHub = EventHub(max_history=0)

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None

        self._lock = asyncio.Lock()
        self._frame_task: Optional[asyncio.Task] = None
        self._screencast_mode: Optional[str] = None  # "screencast" | "screenshot"

        self.status = "idle"  # idle | starting | running | error
        self.status_message = ""
        self.last_error: Optional[str] = None

        self._latest_frame: Optional[str] = None  # base64 JPEG
        self._last_publish_ts = 0.0

    # ── Read-only accessors ────────────────────────────────────────────────

    @property
    def page(self):
        """The live Playwright page (or None when not running)."""
        return self._page

    @property
    def context(self):
        return self._context

    def latest_frame(self) -> Optional[str]:
        """Most recent screencast frame as a base64 JPEG string."""
        return self._latest_frame

    def snapshot(self) -> dict:
        """Small JSON-able status snapshot for the API."""
        return {
            "status": self.status,
            "message": self.status_message,
            "error": self.last_error,
            "has_frame": bool(self._latest_frame),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def ensure_started(self, url: str = WHATSAPP_URL) -> dict:
        """Start the browser if it is not already running (idempotent)."""
        if self._page is not None and self.status in ("running", "starting"):
            return self.snapshot()
        return await self.start(url=url)

    async def start(self, url: str = WHATSAPP_URL, headless: bool = True) -> dict:
        """Launch the headless browser, navigate to ``url``, start streaming."""
        async with self._lock:
            if self._page is not None and self.status in ("running", "starting"):
                # Already up — (re)navigate if a different URL was requested.
                if url and self._page.url.rstrip("/") != url.rstrip("/"):
                    try:
                        await self._page.goto(
                            url, wait_until="domcontentloaded", timeout=60000
                        )
                    except Exception as exc:  # navigation errors are non-fatal here
                        logger.warning("browser view navigate failed: %s", exc)
                return self.snapshot()
            await self._shutdown_locked()

        self._set_status("starting", "Launching headless browser…")

        try:
            from patchright.async_api import async_playwright

            pw = await async_playwright().start()
            browser = await pw.chromium.launch(headless=headless, args=BROWSER_ARGS)
            context = await browser.new_context(
                viewport=dict(VIEWPORT),
                locale="en-US",
                timezone_id="America/New_York",
                permissions=["notifications"],
                user_agent=USER_AGENT,
            )
            await context.add_init_script(_STEALTH_SCRIPT)
            page = await context.new_page()
            cdp = await context.new_cdp_session(page)
            await cdp.send("Page.enable")

            await page.goto(url, wait_until="domcontentloaded", timeout=60000)

            async with self._lock:
                self._pw, self._browser = pw, browser
                self._context, self._page, self._cdp = context, page, cdp

            # Seed one frame immediately so clients see something right away.
            try:
                raw = await page.screenshot(type="jpeg", quality=55)
                self._latest_frame = base64.b64encode(raw).decode()
            except Exception:
                pass

            # Preferred path: CDP screencast. Fallback: screenshot polling.
            self._screencast_mode = "screencast"
            try:
                cdp.on("Page.screencastFrame", self._on_screencast_frame)
                await cdp.send("Page.startScreencast", dict(SCREENCAST_PARAMS))
            except Exception as exc:
                logger.warning(
                    "CDP screencast unavailable (%s) — using screenshot polling", exc
                )
                self._screencast_mode = "screenshot"
                self._frame_task = asyncio.create_task(self._screenshot_loop())

            self._set_status("running", f"Browser view running — {url}")
            logger.info("browser view started: %s (mode=%s)", url, self._screencast_mode)
            return self.snapshot()

        except Exception as exc:
            logger.exception("browser view failed to start")
            self.last_error = str(exc)
            self._set_status("error", f"Failed to start browser view: {exc}")
            try:
                async with self._lock:
                    await self._shutdown_locked()
            except Exception:
                pass
            return self.snapshot()

    async def stop(self) -> dict:
        """Stop the browser and reset state."""
        async with self._lock:
            await self._shutdown_locked()
        self.last_error = None
        self._set_status("idle", "Browser view stopped")
        logger.info("browser view stopped")
        return self.snapshot()

    async def _shutdown_locked(self) -> None:
        """Tear down browser resources (caller must hold ``self._lock``)."""
        if self._frame_task is not None:
            self._frame_task.cancel()
            try:
                await self._frame_task
            except (asyncio.CancelledError, Exception):
                pass
            self._frame_task = None

        if self._cdp is not None:
            try:
                await self._cdp.send("Page.stopScreencast")
            except Exception:
                pass
            try:
                await self._cdp.detach()
            except Exception:
                pass
        self._cdp = None

        for obj, method in (
            (self._context, "close"),
            (self._browser, "close"),
            (self._pw, "stop"),
        ):
            if obj is not None:
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass

        self._context = None
        self._browser = None
        self._pw = None
        self._page = None
        self._latest_frame = None
        self._screencast_mode = None

    # ── Status / event helpers ─────────────────────────────────────────────

    def _set_status(self, status: str, message: str) -> None:
        self.status = status
        self.status_message = message
        event = {
            "type": "status",
            "status": status,
            "message": message,
            "error": self.last_error,
        }
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.events.publish(event))
            # Log to terminal instead of frontend
            level = logging.INFO
            if status == "error":
                level = logging.ERROR
            elif status == "starting":
                level = logging.INFO
            logger.log(level, message)
        except RuntimeError:
            pass

    async def subscribe(self) -> asyncio.Queue:
        return await self.events.subscribe()

    async def unsubscribe(self, queue: asyncio.Queue) -> None:
        await self.events.unsubscribe(queue)

    # ── Frame capture ──────────────────────────────────────────────────────

    def _on_screencast_frame(self, params: dict) -> None:
        """Sync CDP callback — schedule frame processing on the loop."""
        try:
            asyncio.get_running_loop().create_task(self._process_frame(params))
        except RuntimeError:
            pass

    async def _process_frame(self, params: dict) -> None:
        session_id = params.get("sessionId")
        data = params.get("data")

        # Ack every screencast frame or Chromium stops sending them.
        if self._screencast_mode == "screencast" and session_id and self._cdp:
            try:
                await self._cdp.send(
                    "Page.screencastFrameAck", {"sessionId": session_id}
                )
            except Exception:
                pass

        if not data:
            return

        self._latest_frame = data
        now = time.monotonic()
        if now - self._last_publish_ts >= MIN_FRAME_INTERVAL:
            self._last_publish_ts = now
            await self.events.publish(
                {"type": "frame", "data": data, "ts": time.time()}
            )

    async def _screenshot_loop(self) -> None:
        """Fallback capture path: poll page.screenshot() while running."""
        while True:
            page = self._page
            if page is None or self.status != "running":
                break
            try:
                raw = await page.screenshot(type="jpeg", quality=55)
                self._latest_frame = base64.b64encode(raw).decode()
                await self.events.publish(
                    {"type": "frame", "data": self._latest_frame, "ts": time.time()}
                )
            except Exception:
                pass
            await asyncio.sleep(0.8)

    # ── Input dispatch ─────────────────────────────────────────────────────

    async def send_input(self, payload: dict) -> dict:
        """Dispatch an input action into the live page.

        Coordinates (``x``/``y``) are normalized 0..1 relative to the
        viewport so the frontend doesn't care about the stream resolution.
        """
        if self._page is None or self._cdp is None or self.status != "running":
            return {"ok": False, "error": "Browser view is not running"}

        action = payload.get("action", "click")
        try:
            if action == "click":
                x = int(float(payload.get("x", 0.5)) * VIEWPORT["width"])
                y = int(float(payload.get("y", 0.5)) * VIEWPORT["height"])
                for event_type in ("mousePressed", "mouseReleased"):
                    await self._cdp.send(
                        "Input.dispatchMouseEvent",
                        {
                            "type": event_type,
                            "x": x,
                            "y": y,
                            "button": "left",
                            "clickCount": 1,
                        },
                    )
            elif action == "scroll":
                await self._cdp.send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": "mouseWheel",
                        "deltaX": int(payload.get("deltaX", 0)),
                        "deltaY": int(payload.get("deltaY", 0)),
                    },
                )
            elif action == "type":
                await self._cdp.send(
                    "Input.insertText", {"text": str(payload.get("text", ""))}
                )
            elif action == "key":
                key = str(payload.get("key", "Enter"))
                code = str(payload.get("code", key))
                for event_type in ("keyDown", "keyUp"):
                    await self._cdp.send(
                        "Input.dispatchKeyEvent",
                        {"type": event_type, "key": key, "code": code},
                    )
            elif action == "navigate":
                url = payload.get("url") or WHATSAPP_URL
                await self._page.goto(
                    url, wait_until="domcontentloaded", timeout=60000
                )
            else:
                return {"ok": False, "error": f"Unknown action: {action}"}
            return {"ok": True}
        except Exception as exc:
            logger.warning("browser input failed (%s): %s", action, exc)
            return {"ok": False, "error": str(exc)}


# Process-wide singleton — the FastAPI app and the WhatsApp connect flow
# share this instance so the QR browser lives for the whole session.
browser_view = BrowserViewManager()

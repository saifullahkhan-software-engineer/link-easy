"""
WhatsApp Web browser automation via Playwright.
FILE: services/whatsapp_browser.py

Handles:
  - Launching a Playwright browser for WhatsApp Web
  - QR-code login flow
  - Session persistence (cookies -> PostgreSQL)
  - Group listing (scraping sidebar)
  - Message scraping from groups
  - Image downloading
  - Message forwarding

All selectors target stable attributes (aria-labels, data-testid, roles)
rather than React hashed class names.
"""
import asyncio
import base64
import io
import json
import os
import random
import re
import time
from typing import Optional

from patchright.async_api import async_playwright, BrowserContext, Page

from core.config import settings
from core.logging_config import get_logger

logger = get_logger(__name__)

# ── Persistent WhatsApp profile ──────────────────────────────────────────────
# WhatsApp Web keeps its device/session keys in IndexedDB, which Playwright's
# ``storage_state()`` does NOT capture (cookies + localStorage only). Restoring
# a "session" from storage_state alone therefore opens a half-broken device,
# and launching a second browser from it is exactly what used to kill a fresh
# connection right after the QR scan.
#
# The fix mirrors the LinkedIn accounts rollout (docs/persistent_profiles_rollout.md):
# one durable Chromium user-data-dir for WhatsApp, opened via
# ``launch_persistent_context``. Cookies, localStorage, IndexedDB and service
# workers all persist to disk continuously — the profile directory itself is
# the session. Every WhatsApp browser (QR view, group scraping, scan task)
# reuses this ONE profile, serialized by the redis profile lock, so there is
# never more than one browser on the account at a time.

WHATSAPP_PROFILE_SUBDIR = "whatsapp"

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--no-first-run",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

STEALTH_SCRIPT = """
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
"""


def whatsapp_profile_dir() -> str:
    """Path of the durable WhatsApp Chromium profile directory."""
    return os.path.join(settings.PROFILE_STORAGE_DIR, WHATSAPP_PROFILE_SUBDIR)


_CHROMIUM_SINGLETON_FILES = ("SingletonLock", "SingletonCookie", "SingletonSocket")


def clear_stale_chromium_singleton(profile_dir: str | None = None) -> bool:
    """Remove Chromium Singleton* files when the locking process is dead.

    A crashed Playwright process leaves ``SingletonLock`` behind. The next
    ``launch_persistent_context`` then fails even though no WhatsApp session
    is actually open. If the lock file points at a still-running PID we
    leave it alone.
    """
    path = profile_dir or whatsapp_profile_dir()
    lock_path = os.path.join(path, "SingletonLock")
    if not os.path.lexists(lock_path):
        return False

    pid = None
    if os.path.islink(lock_path):
        try:
            target = os.readlink(lock_path)
        except OSError:
            target = ""
        if "-" in target:
            try:
                pid = int(target.rsplit("-", 1)[-1])
            except ValueError:
                pid = None
    if pid is not None:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            pass
        except PermissionError:
            # Process exists (possibly another user) — do not steal.
            logger.info("Chromium SingletonLock pid %s is still alive — leaving it", pid)
            return False
        except OSError:
            # Conservative: unknown error, do not delete a maybe-live lock.
            return False
        else:
            logger.info("Chromium SingletonLock pid %s is still alive — leaving it", pid)
            return False

    removed = False
    for name in _CHROMIUM_SINGLETON_FILES:
        candidate = os.path.join(path, name)
        try:
            if os.path.isdir(candidate) and not os.path.islink(candidate):
                import shutil

                shutil.rmtree(candidate)
            else:
                os.unlink(candidate)
            removed = True
        except FileNotFoundError:
            continue
        except OSError as exc:
            logger.warning("Could not remove stale Chromium %s: %s", name, exc)
    if removed:
        logger.warning("🧹 Cleared stale Chromium singleton files in %s", path)
    return removed


def ensure_whatsapp_profile_dir() -> str:
    """Create the WhatsApp profile dir with restrictive 0o700 permissions.

    The profile contains live session material on disk (cookies, IndexedDB),
    so it is treated as secret — same policy as the per-account LinkedIn
    profiles in automation/browser.py. Idempotent.
    """
    path = whatsapp_profile_dir()
    os.makedirs(path, mode=0o700, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


async def launch_whatsapp_persistent(headless: bool = True) -> tuple:
    """Launch Chromium on the durable WhatsApp profile.

    The QR login happens inside this profile and every later launch
    (group scraping, periodic scans) restores the same device session from
    disk — no storage_state round-trip, and never two browsers racing on the
    same account.

    Callers MUST hold the ``profile_lock:whatsapp`` redis lock (see
    worker/profile_lock.py): Chromium allows only one process per
    user-data-dir.

    Returns:
        (playwright_instance, context, page) — a persistent context has no
        separate Browser object; clean up with ``safe_close(pw, context)``.
    """
    profile_dir = ensure_whatsapp_profile_dir()

    pw = await async_playwright().start()
    try:
        context: BrowserContext = await pw.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
            permissions=["notifications"],
            user_agent=USER_AGENT,
            args=LAUNCH_ARGS,
        )
    except Exception:
        # Don't leak the driver subprocess if the context launch fails
        # (e.g. the profile dir is SingletonLocked by another process).
        try:
            await pw.stop()
        except Exception:
            pass
        raise

    # A restored profile may reopen previous tabs; use the first page if one
    # exists, otherwise create one, and close any extras.
    page: Page = context.pages[0] if context.pages else await context.new_page()
    for extra in [p for p in context.pages if p is not page]:
        try:
            await extra.close()
        except Exception:
            pass

    await context.add_init_script(STEALTH_SCRIPT)

    return pw, context, page

# ── WhatsApp Web selectors (stable attributes, no CSS classes) ──────────────

# QR code canvas — appears when not logged in
QR_CANVAS_SELECTOR = 'canvas[aria-label="Scan me!"], canvas, div[data-testid="qrcode"]'

# The sidebar pane that holds the chat list.  ``#pane-side`` has survived every
# WhatsApp Web redesign — the old ``data-testid`` attributes were phased out
# years ago and aria-label copy varies with locale, so it is the primary
# "you are logged in" indicator.
PANE_SIDE_SELECTOR = '#pane-side'

# The main chat list sidebar
CHAT_LIST_SELECTOR = (
    '#pane-side, #side, div[aria-label="Chat list"], div[role="grid"][aria-label], '
    'div[data-testid="chat-list"]'
)

# Individual chat rows inside the sidebar.  WhatsApp has used both ``row``
# and ``listitem`` for the virtualized sidebar across recent rollouts.
CHAT_ROW_SELECTOR = (
    'div[role="row"], div[role="listitem"], '
    'div[data-testid="cell-frame-container"]'
)

# Chat name/title inside a row
CHAT_NAME_SELECTOR = (
    'span[data-testid="cell-frame-title"], span[dir="auto"], '
    '[role="gridcell"] span[dir="auto"]'
)

# Search box for finding groups
SEARCH_BOX_SELECTOR = 'div[data-testid="chat-list-search"], div[contenteditable="true"][data-tab="3"]'

# Message containers in the conversation pane. Keep the wrapper and data-id
# selectors separate: in current WhatsApp Web builds the same logical message
# is often represented by BOTH an outer ``msg-container`` and an inner
# ``data-id`` row. Querying the comma-joined selector and treating every result
# as a message doubled three real messages into six candidates.
MSG_CONTAINER_WRAPPER_SELECTOR = 'div[data-testid="msg-container"]'
MSG_CONTAINER_ID_SELECTOR = (
    'div[role="row"][data-id], '
    'div[data-id][tabindex="-1"]'
)
MSG_CONTAINER_SELECTOR = (
    f'{MSG_CONTAINER_WRAPPER_SELECTOR}, {MSG_CONTAINER_ID_SELECTOR}'
)
# Last-resort selector used when a WhatsApp build wraps the visible messages
# without either of the stable attributes above. It is only used when the
# primary selectors return one (or zero) logical items, then records are
# filtered by actual message text/image content.
MSG_CONTAINER_FALLBACK_SELECTOR = (
    '#main div[data-id], '
    'div[data-testid="conversation-panel-wrapper"] div[data-id], '
    'div[data-testid="conversation-panel-messages"] div[data-id]'
)

# Message text content
MSG_TEXT_SELECTOR = (
    'span.selectable-text, span[data-testid="msg-text"], '
    'span[data-lexical-text="true"]'
)

# Image inside a message
MSG_IMAGE_SELECTOR = (
    'img[data-testid="image-thumb"], div[data-testid="image-thumb"] img, '
    'div[data-testid="media-caption"] img, img.selectable-img, div._amkd img, '
    'img[src^="blob:"], div[role="button"] img[src^="blob:"]'
)

# Message composer. Keep every generic role-based fallback scoped to ``#main``
# or ``footer``: WhatsApp's sidebar search is also a contenteditable textbox,
# and selecting it here makes a live-chat "send" silently type into Search.
MSG_INPUT_SELECTOR = (
    '#main div[contenteditable="true"][data-tab="10"], '
    '#main footer div[contenteditable="true"][role="textbox"], '
    '#main div[contenteditable="true"][role="textbox"], '
    'footer div[contenteditable="true"][data-tab="10"]'
)

# Current WhatsApp builds expose an aria-label or data-icon; retain the older
# data-testid forms for profiles that have not received that rollout yet.
SEND_BUTTON_SELECTOR = (
    '#main button[aria-label="Send"], #main button[data-tab="11"], '
    '#main span[data-icon="send"], button[data-testid="compose-btn-send"], '
    'span[data-testid="send"]'
)

# Chat header / group name in conversation
CHAT_HEADER_SELECTOR = (
    'div[data-testid="conversation-header"], '
    '#main header, '
    'header span[dir="auto"]'
)

# Loading / spinner indicators
LOADING_SELECTOR = 'div[data-testid="progress-bar"], span[data-testid="loading"]'

# Main pane (conversation area). ``#main`` is the long-lived WhatsApp Web
# selector; the data-testid fallbacks support older builds.
MAIN_PANE_SELECTOR = (
    '#main, div[data-testid="conversation-panel-wrapper"], '
    'div[data-testid="conversation-panel"], '
    '[role="main"][data-testid]'
)

# Candidate selectors that indicate "you are logged in" (the main interface).
# Ordered from most- to least-reliable; the first match wins.  Old data-testid
# entries are kept as fallbacks for older web builds.
LOGGED_IN_SELECTORS = (
    PANE_SIDE_SELECTOR,
    '#side',
    'div[aria-label="Chat list"]',
    'div[data-testid="chat-list"]',
    'header[data-testid="chatlist-header"]',
)

# Comma-joined version for single query_selector calls.
LOGGED_IN_SELECTOR = ', '.join(LOGGED_IN_SELECTORS)


async def launch_whatsapp_browser(
    headless: bool = False,
    storage_state: Optional[dict] = None,
) -> tuple:
    """Launch a Playwright Chromium browser for WhatsApp Web.

    .. deprecated::
        Kept only for legacy/CLI flows. All real callers now use
        :func:`launch_whatsapp_persistent` — ``storage_state`` alone cannot
        restore a WhatsApp Web session (device keys live in IndexedDB), and
        launching a second stateless browser on the account is what used to
        break fresh connections.

    Args:
        headless: False for QR login flow (user needs to see QR), True for background tasks.
        storage_state: Optional saved browser state (cookies, localStorage) to restore.

    Returns:
        (playwright_instance, context, page)
    """
    pw = await async_playwright().start()

    launch_options = {
        "headless": headless,
        "args": LAUNCH_ARGS,
    }

    browser = await pw.chromium.launch(**launch_options)

    context_options = {
        "viewport": {"width": 1280, "height": 900},
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "permissions": ["notifications"],
        "user_agent": USER_AGENT,
    }

    if storage_state:
        context_options["storage_state"] = storage_state

    context: BrowserContext = await browser.new_context(**context_options)
    page: Page = await context.new_page()

    # Basic stealth patches for WhatsApp Web
    await context.add_init_script(STEALTH_SCRIPT)

    return pw, context, page


async def navigate_to_whatsapp(page: Page) -> None:
    """Navigate to WhatsApp Web and wait for the page to load."""
    logger.info("🌐 Navigating to WhatsApp Web...")
    await page.goto("https://web.whatsapp.com/", wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(3)
    logger.info(f"📍 WhatsApp Web URL: {page.url}")


async def is_logged_in(page: Page) -> bool:
    """Check if the page is currently logged into WhatsApp Web.

    Tries each candidate selector and requires the element to be *visible* —
    ``query_selector`` alone also matches elements that are mounted but
    hidden while the loading/progress screen is up, which caused false
    negatives/positives in the QR-watch loop.
    """
    for selector in LOGGED_IN_SELECTORS:
        try:
            el = await page.query_selector(selector)
            if el is not None and await el.is_visible():
                return True
        except Exception:  # page may be mid-navigation
            continue
    return False


async def wait_for_login(page: Page, timeout_seconds: float = 30.0) -> bool:
    """Poll for the logged-in UI for up to ``timeout_seconds``.

    WhatsApp Web routinely takes 5–15 seconds after ``domcontentloaded``
    before the chat list mounts. Callers used to make a single
    ``is_logged_in`` check after ~3 seconds and misread slow loads as
    "session expired", which then flipped a perfectly good connection to
    disconnected. Always wait instead.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if await is_logged_in(page):
                return True
        except Exception:  # page may be mid-navigation
            pass
        await asyncio.sleep(1.5)
    return False


async def is_showing_qr(page: Page) -> bool:
    """True when WhatsApp Web is on the QR / login landing screen.

    Used to distinguish a genuinely logged-out session (→ safe to mark
    disconnected) from a page that is still loading (→ leave the session
    untouched and retry later).
    """
    for selector in (
        'canvas[aria-label="Scan me!"]',
        'div[data-testid="qrcode"]',
        'img[alt*="QR" i]',
    ):
        try:
            el = await page.query_selector(selector)
            if el is not None and await el.is_visible():
                return True
        except Exception:
            continue

    # Fallback: a visible generic QR canvas with no logged-in markers at all.
    try:
        canvas = await page.query_selector("canvas")
        if canvas is not None and await canvas.is_visible():
            for selector in LOGGED_IN_SELECTORS:
                el = await page.query_selector(selector)
                if el is not None:
                    try:
                        if await el.is_visible():
                            return False
                    except Exception:
                        # A mounted selector that cannot report visibility is
                        # safer to treat as a login marker than to call a
                        # half-hydrated canvas a fresh QR code.
                        return False
            return True
    except Exception:
        pass
    return False


async def wait_for_full_whatsapp_surface(
    page: Page, timeout_seconds: float = 60.0
) -> bool:
    """Wait for both the sidebar and the conversation shell to be visible.

    ``#pane-side`` alone appears before WhatsApp finishes hydrating.  Reusing
    the profile at that point is a race: the next live-chat browser inherits a
    half-loaded IndexedDB/app shell and chat rows cannot be opened.  This
    helper is intentionally visibility-based and is used after QR login and
    before live chat starts.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if not await is_logged_in(page):
                await asyncio.sleep(0.75)
                continue
            sidebar_visible = False
            for selector in LOGGED_IN_SELECTORS:
                sidebar = await page.query_selector(selector)
                if sidebar is not None and await sidebar.is_visible():
                    sidebar_visible = True
                    break
            main_visible = False
            for selector in MAIN_PANE_SELECTOR.split(","):
                candidate = await page.query_selector(selector.strip())
                if candidate is not None and await candidate.is_visible():
                    main_visible = True
                    break
            if sidebar_visible and main_visible:
                return True
        except Exception:
            # Navigation/React hydration may detach one of the nodes briefly.
            pass
        await asyncio.sleep(0.75)
    return False


async def wait_for_whatsapp_surface(
    page: Page, timeout_seconds: float = 45.0
) -> str:
    """Wait until WhatsApp has rendered either QR or the full logged-in UI.

    ``domcontentloaded`` only means the shell HTML arrived.  On a cold
    Chromium profile the React chat application can take another 10–30
    seconds to mount.  Starting another browser during that gap makes the
    connection look broken and leaves live chat with an empty sidebar.  This
    helper distinguishes the expected QR surface from the authenticated app
    without treating a slow page as a logged-out session.
    """
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            if await is_logged_in(page):
                remaining = max(0.5, deadline - time.monotonic())
                return "connected" if await wait_for_full_whatsapp_surface(
                    page, timeout_seconds=remaining
                ) else "timeout"
            if await is_showing_qr(page):
                return "qr"
        except Exception:
            # Navigation and React hydration can detach selectors temporarily.
            pass
        await asyncio.sleep(0.75)
    return "timeout"


async def wait_for_qr_scan(page: Page, max_wait_seconds: int = 120) -> bool:
    """Wait for the user to scan the QR code and WhatsApp Web to fully load.

    Returns True if login succeeded, False on timeout.
    """
    logger.info("📱 Waiting for QR code scan...")
    deadline = time.monotonic() + max_wait_seconds

    while time.monotonic() < deadline:
        try:
            if await is_logged_in(page):
                if await wait_for_full_whatsapp_surface(page, timeout_seconds=45):
                    logger.info("✅ WhatsApp Web logged in — full chat surface detected")
                    await asyncio.sleep(2)  # let the UI fully settle
                    return True
                logger.info("⏳ WhatsApp sidebar is ready; waiting for the conversation shell")

            # Check if we're still on the landing / QR page
            current_url = page.url
            if "web.whatsapp.com" not in current_url:
                logger.warning(f"⚠️  Redirected away from WhatsApp: {current_url}")
                return False

        except Exception as e:
            logger.debug(f"QR scan poll error: {e}")

        await asyncio.sleep(2)

    logger.error("❌ QR scan timed out")
    return False


async def get_storage_state(context: BrowserContext) -> dict:
    """Extract cookies and storage state from the browser context for persistence."""
    state = await context.storage_state()
    return state


async def restore_session_and_navigate(
    page: Page, storage_state: Optional[dict]
) -> bool:
    """Navigate to WhatsApp Web and check if a previously saved session is valid.

    Returns True if already logged in, False if re-login is needed.
    """
    await navigate_to_whatsapp(page)
    await asyncio.sleep(3)

    if await is_logged_in(page):
        logger.info("✅ WhatsApp session is still valid")
        return True

    logger.info("⚠️  WhatsApp session expired — needs re-login")
    return False


# ── Group scraping ───────────────────────────────────────────────────────────


async def fetch_group_list(page: Page, search: Optional[str] = None) -> list[dict]:
    """Scrape the first ten chats/groups, or find a specific chat/group.

    WhatsApp's sidebar is a virtualized list, so the first page is intentionally
    small. When ``search`` is supplied, use WhatsApp Web's search field to find
    older groups as well.
    """
    logger.info("📋 Fetching WhatsApp group list from sidebar...")

    # Wait for the chat list to be visible
    try:
        await page.wait_for_selector(CHAT_LIST_SELECTOR, timeout=15000)
    except Exception:
        logger.warning("⚠️  Chat list not found — is WhatsApp logged in?")
        return []

    await asyncio.sleep(2)

    if search:
        # Search both chats and groups; the result rows use the same selectors.
        search_box = page.locator('div[contenteditable="true"][data-tab], input[placeholder*="Search"]')
        try:
            await search_box.first.fill(search)
            await asyncio.sleep(2)
        except Exception:
            logger.warning("Could not open WhatsApp search field")

    groups = []
    max_rounds = 10 if search else 1

    # The sidebar is virtualized. Load only the first ten rows by default;
    # search mode may scroll a little to expose all matching results.
    for _ in range(max_rounds):
        try:
            # Collect visible chat rows
            rows = await page.query_selector_all(CHAT_ROW_SELECTOR)
            for row in rows:
                try:
                    # Extract group name from the title span
                    name_el = await row.query_selector(CHAT_NAME_SELECTOR)
                    if not name_el:
                        # Try broader approach — any span with text
                        spans = await row.query_selector_all("span[dir='auto']")
                        for span in spans:
                            text = (await span.inner_text()).strip()
                            if text and len(text) > 1:
                                name_el = span
                                break

                    if not name_el:
                        continue

                    group_name = (await name_el.inner_text()).strip()

                    if not group_name or len(group_name) < 2:
                        continue

                    # Try to extract WhatsApp internal ID from the row
                    whatsapp_id = None
                    try:
                        data_id = await row.get_attribute("data-id")
                        if data_id:
                            whatsapp_id = data_id
                    except Exception:
                        pass

                    # Also try aria-label for the ID
                    if not whatsapp_id:
                        try:
                            aria = await row.get_attribute("aria-label")
                            if aria:
                                whatsapp_id = aria
                        except Exception:
                            pass

                    # Deduplicate
                    if not any(g["group_name"] == group_name for g in groups):
                        groups.append({
                            "group_name": group_name,
                            "whatsapp_id": whatsapp_id,
                        })
                except Exception:
                    continue

            # Scroll the chat list down to load more
            chat_list = await page.query_selector(CHAT_LIST_SELECTOR)
            if chat_list:
                await chat_list.evaluate("el => el.scrollTop = el.scrollHeight")
            await asyncio.sleep(1)
        except Exception:
            break

    logger.info(f"📋 Found {len(groups)} chat contacts/groups")
    return groups


async def navigate_to_group(page: Page, group_name: str) -> bool:
    """Navigate to a specific group by searching for it and clicking it.

    Returns True if the group was found and opened.
    """
    logger.info(f"🔍 Navigating to group: {group_name}")

    try:
        # Use search box to find the group
        search_box = await page.query_selector(SEARCH_BOX_SELECTOR)
        if not search_box:
            # Try alternative search approach
            search_boxes = await page.query_selector_all('div[contenteditable="true"]')
            for sb in search_boxes:
                try:
                    tab_index = await sb.get_attribute("data-tab")
                    if tab_index == "3":  # chat search tab
                        search_box = sb
                        break
                except Exception:
                    continue

        if search_box:
            # Clear existing text and type group name
            await search_box.click()
            await asyncio.sleep(0.3)

            # Select all and delete
            await page.keyboard.press("Control+a")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.3)

            await search_box.type(group_name, delay=50)
            await asyncio.sleep(1.5)

            # Click the first matching chat result
            chat_rows = await page.query_selector_all(CHAT_ROW_SELECTOR)
            for row in chat_rows:
                try:
                    name_el = await row.query_selector(CHAT_NAME_SELECTOR)
                    if name_el:
                        name_text = (await name_el.inner_text()).strip()
                        if group_name.lower() in name_text.lower():
                            await row.click()
                            await asyncio.sleep(2)
                            logger.info(f"✅ Opened group: {group_name}")
                            return True
                except Exception:
                    continue

        # If search box approach fails, try scrolling the sidebar
        logger.info("🔍 Search box approach failed, trying sidebar scroll...")
        for _ in range(15):
            rows = await page.query_selector_all(CHAT_ROW_SELECTOR)
            for row in rows:
                try:
                    name_el = await row.query_selector(CHAT_NAME_SELECTOR)
                    if name_el:
                        name_text = (await name_el.inner_text()).strip()
                        if group_name.lower() in name_text.lower():
                            await row.click()
                            await asyncio.sleep(2)
                            logger.info(f"✅ Opened group via scroll: {group_name}")
                            return True
                except Exception:
                    continue

            chat_list = await page.query_selector(CHAT_LIST_SELECTOR)
            if chat_list:
                await chat_list.evaluate("el => el.scrollTop += 500")
            await asyncio.sleep(0.8)

    except Exception as e:
        logger.error(f"Error navigating to group '{group_name}': {e}")

    logger.warning(f"⚠️  Could not find group: {group_name}")
    return False


async def _message_id(container) -> str | None:
    """Read the canonical WhatsApp id from a rendered message element.

    Some WhatsApp builds wrap the logical message (with ``data-id``) inside an
    outer ``msg-container`` that also has a generated HTML ``id``. The HTML id
    identifies the wrapper, not the message. Prefer the descendant WhatsApp
    ``data-id`` before that generic id so the outer and inner DOM nodes collapse
    to the same logical message during deduplication.
    """
    try:
        msg_id = await container.get_attribute("data-id")
        if msg_id:
            return msg_id

        # Depending on the release, the data-id row can wrap msg-container
        # instead of sitting below it.
        try:
            ancestor_id = await container.evaluate("""(element) => {
                const row = element.closest(
                    '[role="row"][data-id], [data-id][tabindex="-1"]'
                );
                return row ? row.getAttribute('data-id') : null;
            }""")
            if ancestor_id:
                return ancestor_id
        except Exception:
            pass

        for selector in (
            '[role="row"][data-id]',
            '[data-id][tabindex="-1"]',
            "[data-id]",
        ):
            try:
                inner = await container.query_selector(selector)
                if inner:
                    inner_id = await inner.get_attribute("data-id")
                    if inner_id:
                        return inner_id
            except Exception:
                continue

        # Last resort for older builds where the actual message node used id.
        return await container.get_attribute("id")
    except Exception:
        return None


def _image_payload_dimensions(payload: str | bytes | None) -> tuple[int, int]:
    """Return decoded image dimensions without leaking malformed media errors."""
    if not payload:
        return (0, 0)
    try:
        from PIL import Image

        if isinstance(payload, bytes):
            image_bytes = payload
        else:
            encoded = str(payload).strip()
            if encoded.startswith("data:"):
                encoded = encoded.split(",", 1)[1]
            encoded = "".join(encoded.split())
            encoded += "=" * (-len(encoded) % 4)
            image_bytes = base64.b64decode(
                encoded.replace("-", "+").replace("_", "/"),
                validate=False,
            )
        with Image.open(io.BytesIO(image_bytes)) as image:
            return tuple(int(value) for value in image.size)
    except Exception:
        return (0, 0)


def _best_image_payload(*payloads: str | bytes | None) -> str | None:
    """Choose the highest-resolution copy of a rendered WhatsApp image.

    WhatsApp sometimes exposes a very small blob thumbnail (for example 32x72)
    even though the element is rendered at several hundred pixels. Previously
    that tiny blob was sent to Tesseract and the element screenshot fallback was
    attempted only when blob download failed. Compare both every time and keep
    the copy containing the most pixels.
    """
    best_payload: str | bytes | None = None
    best_score = -1
    for payload in payloads:
        if not payload:
            continue
        width, height = _image_payload_dimensions(payload)
        # Unknown-but-present data remains a valid last-resort payload.
        score = width * height if width and height else 0
        if best_payload is None or score > best_score:
            best_payload = payload
            best_score = score

    if best_payload is None:
        return None
    if isinstance(best_payload, bytes):
        return base64.b64encode(best_payload).decode("ascii")
    return str(best_payload)


async def _extract_message_container(container) -> dict | None:
    """Extract one rendered WhatsApp message.

    This is deliberately separate from the scrolling loop. WhatsApp virtualizes
    the conversation and can detach older elements as the list is scrolled, so
    every element is read while it is still rendered.
    """
    try:
        msg_id = await _message_id(container)

        sender_name = None
        try:
            sender_el = await container.query_selector(
                'span[data-testid="msg-sender"], span[aria-label]'
            )
            if sender_el:
                sender_name = (await sender_el.inner_text()).strip()
        except Exception:
            pass

        is_image = False
        img_el = None
        try:
            # Selector-list order does not imply priority in querySelectorAll;
            # it returns document order. The former "first image wins" logic
            # therefore selected emoji, avatars, and 32px link icons before the
            # actual flyer. Inspect every image and choose the largest likely
            # media element, strongly preferring WhatsApp's image-thumb region.
            candidates = []
            seen_candidate_objects: set[int] = set()
            for selector in (MSG_IMAGE_SELECTOR, "img"):
                try:
                    selected = await container.query_selector_all(selector)
                except Exception:
                    selected = []
                for candidate in selected:
                    object_key = id(candidate)
                    if object_key not in seen_candidate_objects:
                        candidates.append(candidate)
                        seen_candidate_objects.add(object_key)

            best_score = -1
            for candidate in candidates:
                try:
                    metadata = await candidate.evaluate("""(img) => {
                        const className = typeof img.className === 'string' ? img.className.toLowerCase() : '';
                        const alt = (img.alt || '').toLowerCase();
                        const isEmoji = img.hasAttribute('data-plain-text') ||
                            className.includes('emoji') || alt.includes('emoji') ||
                            !!img.closest('[data-plain-text]');
                        if (isEmoji) return {valid: false, score: 0};

                        const rect = img.getBoundingClientRect();
                        const naturalWidth = img.naturalWidth || img.width || 0;
                        const naturalHeight = img.naturalHeight || img.height || 0;
                        // Validate generic candidates by their DISPLAY size.
                        // Avatars may have a 640px source but render at 40px.
                        const displayWidth = Math.round(rect.width || 0) || naturalWidth;
                        const displayHeight = Math.round(rect.height || 0) || naturalHeight;
                        const explicitMedia = img.matches(
                            'img[data-testid="image-thumb"], img.selectable-img'
                        ) || !!img.closest(
                            'div[data-testid="image-thumb"], div[data-testid="media-caption"], div._amkd'
                        );
                        const displayArea = displayWidth * displayHeight;
                        const sourceArea = naturalWidth * naturalHeight;
                        // Non-media images below 80px are avatars/icons. A real
                        // image-thumb is retained even when WhatsApp exposes a
                        // low-res blob because its rendered screenshot is used.
                        const valid = explicitMedia || (
                            Math.min(displayWidth, displayHeight) >= 80 && displayArea >= 10000
                        );
                        return {
                            valid,
                            width: displayWidth,
                            height: displayHeight,
                            explicitMedia,
                            score: (explicitMedia ? 1000000000 : 0) +
                                Math.max(displayArea, sourceArea),
                        };
                    }""")
                    # Simple browser doubles used by tests may return a boolean.
                    if isinstance(metadata, bool):
                        valid = metadata
                        score = 1
                    else:
                        valid = bool(metadata and metadata.get("valid"))
                        score = int((metadata or {}).get("score") or 0)
                    if valid and score > best_score:
                        img_el = candidate
                        best_score = score
                except Exception:
                    continue

            is_image = img_el is not None
        except Exception:
            pass

        message_text = None
        raw_image_bytes = None

        if is_image and img_el is not None:
            try:
                raw_image_bytes = await img_el.evaluate("""async (img) => {
                    try {
                        if (img.src && img.src.startsWith('blob:')) {
                            try {
                                const resp = await fetch(img.src);
                                if (resp.ok) {
                                    const blob = await resp.blob();
                                    return await new Promise((resolve) => {
                                        const reader = new FileReader();
                                        reader.onloadend = () => resolve(reader.result);
                                        reader.readAsDataURL(blob);
                                    });
                                }
                            } catch (e) {
                                // blob fetch blocked by CSP or permissions — fall through to canvas
                            }
                        }
                        if (img.src && img.src.startsWith('data:image')) {
                            return img.src;
                        }
                        const w = img.naturalWidth || img.clientWidth || img.width || 0;
                        const h = img.naturalHeight || img.clientHeight || img.height || 0;
                        if (w > 0 && h > 0) {
                            const canvas = document.createElement('canvas');
                            canvas.width = w;
                            canvas.height = h;
                            const ctx = canvas.getContext('2d');
                            ctx.drawImage(img, 0, 0, w, h);
                            return canvas.toDataURL('image/png');
                        }
                    } catch (e) {
                        return null;
                    }
                    return null;
                }""")
            except Exception as exc:
                logger.debug("Image download evaluate failed: %s", exc)

            # Always capture the rendered element as well. WhatsApp's blob URL
            # can point at a 32-70px preview while the browser has already
            # painted a much larger, OCR-readable image. Keep whichever decoded
            # copy has the greater pixel area.
            screenshot_bytes = None
            try:
                screenshot_bytes = await img_el.screenshot()
            except Exception as exc:
                logger.debug("Image screenshot capture failed: %s", exc)

            raw_image_bytes = _best_image_payload(raw_image_bytes, screenshot_bytes)
            if raw_image_bytes:
                selected_width, selected_height = _image_payload_dimensions(raw_image_bytes)
                logger.debug(
                    "Selected WhatsApp message image %sx%s for OCR",
                    selected_width,
                    selected_height,
                )

            try:
                text_el = await container.query_selector(MSG_TEXT_SELECTOR)
                if text_el:
                    message_text = (await text_el.inner_text()).strip()
            except Exception:
                pass
            message_type = "image"
        else:
            try:
                text_el = await container.query_selector(MSG_TEXT_SELECTOR)
                if text_el:
                    message_text = (await text_el.inner_text()).strip()
            except Exception:
                pass
            message_type = "text"

        timestamp = None
        try:
            time_el = await container.query_selector(
                'span[data-testid="msg-time"], div[data-testid="msg-meta"] span'
            )
            if time_el:
                timestamp = (await time_el.inner_text()).strip()
        except Exception:
            pass

        # Current WhatsApp rows commonly put direction and copy metadata on an
        # ancestor of the inner message container. Keep the direct attributes
        # as a fallback for older builds and fake-DOM tests.
        is_outgoing = False
        try:
            row_class = (await container.get_attribute("class") or "").split()
            data_id = (await container.get_attribute("data-id") or "").lower()
            is_outgoing = "message-out" in row_class or data_id.startswith("true_")
        except Exception:
            pass

        try:
            row_metadata = await container.evaluate(
                """(element) => {
                    const directionRow = element.closest('.message-in, .message-out');
                    const idRow = element.closest('[data-id]') || element;
                    const copyable = element.closest('[data-pre-plain-text]') ||
                        element.querySelector('[data-pre-plain-text]');
                    return {
                        outgoing: directionRow?.classList.contains('message-out') ||
                            (idRow.getAttribute('data-id') || '').toLowerCase().startsWith('true_'),
                        prePlainText: copyable?.getAttribute('data-pre-plain-text') || null,
                    };
                }"""
            )
            if isinstance(row_metadata, dict):
                is_outgoing = is_outgoing or bool(row_metadata.get("outgoing"))
                if not timestamp:
                    pre_plain_text = str(row_metadata.get("prePlainText") or "")
                    match = re.match(r"^\[([^\]]+)]", pre_plain_text)
                    if match:
                        timestamp = match.group(1).strip()
        except Exception:
            pass

        if not message_text and not raw_image_bytes:
            return None

        return {
            "whatsapp_message_id": msg_id,
            "sender_name": sender_name,
            "message_text": message_text,
            "message_type": message_type,
            "timestamp": timestamp,
            "is_outgoing": is_outgoing,
            "raw_image_bytes": raw_image_bytes,
        }
    except Exception as exc:
        logger.debug("Error extracting message: %s", exc)
        return None


async def _query_message_containers(page: Page) -> list:
    """Return one canonical DOM element per rendered logical message.

    Current WhatsApp builds commonly match both an outer ``msg-container`` and
    its inner data-id row. Returning a comma-joined query's raw result therefore
    reports two containers for every real message. Prefer multiple wrappers;
    use inner data-id rows only for the build shape with one shared wrapper.
    """
    try:
        wrappers = await page.query_selector_all(MSG_CONTAINER_WRAPPER_SELECTOR)
    except Exception:
        wrappers = []

    try:
        id_rows = await page.query_selector_all(MSG_CONTAINER_ID_SELECTOR)
    except Exception:
        id_rows = []

    if len(wrappers) > 1:
        if id_rows:
            logger.debug(
                "📨 Canonicalized WhatsApp DOM: %s wrappers + %s nested id rows -> %s messages",
                len(wrappers),
                len(id_rows),
                len(wrappers),
            )
        return wrappers

    # Some releases expose one conversation-level msg-container and put each
    # actual message in a data-id row beneath it.
    if len(id_rows) > len(wrappers):
        return id_rows
    if wrappers:
        return wrappers
    if id_rows:
        return id_rows

    try:
        fallback = await page.query_selector_all(MSG_CONTAINER_FALLBACK_SELECTOR)
        if fallback:
            logger.debug(
                "📨 Stable WhatsApp message selectors returned no items; using %s fallback rows",
                len(fallback),
            )
            return fallback
    except Exception:
        pass

    return []


async def _scroll_message_history_up(page: Page) -> bool:
    """Scroll the conversation upward by a bounded amount.

    WhatsApp virtualizes its message list. Merely asking for the DOM children
    therefore often returns only the newest visible message, even when the
    filter asks for 20 or 50. Find the scrollable ancestor of a message and
    move less than one viewport at a time so adjacent windows overlap.
    """
    scroll_result = None
    try:
        scroll_result = await page.evaluate(
            """(selector) => {
                const message = document.querySelector(selector);
                if (!message) return {moved: false, atTop: true};

                let scrollable = null;
                for (let node = message; node && node !== document.body; node = node.parentElement) {
                    const style = window.getComputedStyle(node);
                    const canScroll = node.scrollHeight > node.clientHeight + 4;
                    const scrollStyle = style.overflowY === 'auto' || style.overflowY === 'scroll';
                    if (canScroll && scrollStyle) {
                        scrollable = node;
                        break;
                    }
                }

                if (!scrollable) return {moved: false, atTop: true};

                const before = scrollable.scrollTop;
                const distance = Math.max(300, Math.floor((scrollable.clientHeight || 600) * 0.65));
                scrollable.scrollTop = Math.max(0, before - distance);
                const after = scrollable.scrollTop;

                return {
                    moved: after < before - 1,
                    atTop: after <= 1,
                };
            }""",
            f"{MSG_CONTAINER_SELECTOR}, {MSG_CONTAINER_FALLBACK_SELECTOR}",
        )
    except Exception as exc:
        logger.debug("Could not scroll WhatsApp message pane with DOM API: %s", exc)

    if isinstance(scroll_result, dict):
        if scroll_result.get("moved"):
            return True
        if scroll_result.get("atTop"):
            return False

    # A fallback for builds that use a non-standard scroll container. The
    # coordinates are inside the normal conversation pane, not the sidebar.
    try:
        await page.mouse.move(850, 500)
        await page.mouse.wheel(0, -900)
        return True
    except Exception:
        return False


async def scrape_messages_from_current_chat(
    page: Page,
    last_message_id: Optional[str] = None,
    last_timestamp: Optional[str] = None,
    message_limit: int = 20,
) -> list[dict]:
    """Scrape the newest bounded set of not-yet-checkpointed messages.

    WhatsApp renders only a window of a conversation. The old implementation
    read that window once, so a group with one visible message always produced
    one candidate regardless of ``latest_messages_limit``. This routine reads
    the current window, scrolls upward in small overlapping steps until the
    requested bound (or the durable cursor) is reached, and extracts each
    element before virtualization can detach it.

    Results are returned newest-first. ``last_message_id`` remains a high-water
    mark: when it becomes visible, only records after it are returned. If the
    cursor is outside the available rendered history, the newest bounded window
    is returned and the worker's persisted-id deduplication handles overlap.
    """
    del last_timestamp  # Kept in the signature for legacy callers/checkpoints.
    message_limit = max(1, min(int(message_limit or 20), 100))

    try:
        await page.wait_for_selector(MSG_CONTAINER_SELECTOR, timeout=10000)
    except Exception:
        # Keep a fallback wait for WhatsApp builds that do not expose the
        # primary role/test-id attributes at all.
        try:
            await page.wait_for_selector(MSG_CONTAINER_FALLBACK_SELECTOR, timeout=10000)
        except Exception:
            logger.warning("⚠️  No message containers found in current chat")
            return []

    await asyncio.sleep(1)

    # WhatsApp's DOM order is oldest -> newest. Keep that order while merging
    # overlapping windows; scrolling upward adds older records at the front.
    ordered_records: list[dict] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()

    def _fallback_content_key(record: dict) -> str:
        txt = (record.get("message_text") or "")[:120]
        img = record.get("raw_image_bytes") or ""
        # Hash only a preview of the base64 to avoid O(n) on 200k strings
        img_preview = img[:256] if isinstance(img, str) else ""
        return f"ct:{hash((txt, img_preview)) & 0xffffffff:08x}"

    async def collect(containers: list, prepend: bool = False) -> int:
        extracted: list[dict] = []
        for container in containers:
            record = await _extract_message_container(container)
            if record is None:
                continue
            msg_id = record.get("whatsapp_message_id")
            if msg_id:
                if msg_id in seen_ids:
                    continue
                seen_ids.add(msg_id)
            else:
                # No stable id (wrapper containers on some builds) — deduplicate
                # by content hash so overlapping scroll windows don't inflate
                # the candidate set 3-4x as seen in the 13:08:19 trace
                # (e.g. the repeating 14/11/10/'iat'/15 pattern).
                key = _fallback_content_key(record)
                if key in seen_content:
                    continue
                seen_content.add(key)
            extracted.append(record)

        if prepend:
            ordered_records[0:0] = extracted
        else:
            ordered_records.extend(extracted)
        return len(extracted)

    async def visible_ids(containers: list) -> set[str]:
        ids = set()
        for container in containers:
            msg_id = await _message_id(container)
            if msg_id:
                ids.add(msg_id)
        return ids

    msg_containers = await _query_message_containers(page)
    logger.info("📨 Found %s rendered message containers", len(msg_containers))
    await collect(msg_containers)

    # At most this many DOM reads are needed for a 100-message cap in normal
    # virtualized windows. The top-of-history and no-progress checks terminate
    # earlier, preventing an accidental unbounded history scrape.
    max_scroll_rounds = min(40, max(4, message_limit + 2))
    for _ in range(max_scroll_rounds):
        current_ids = await visible_ids(msg_containers)
        cursor_visible = bool(last_message_id and last_message_id in current_ids)

        # Once the cursor is visible, all newer messages are in the current
        # window/overlap. Without a cursor, the requested number is enough.
        if cursor_visible or len(ordered_records) >= message_limit:
            break

        moved = await _scroll_message_history_up(page)
        if not moved:
            break
        await asyncio.sleep(0.7)

        next_containers = await _query_message_containers(page)
        if not next_containers:
            break
        added = await collect(next_containers, prepend=True)
        msg_containers = next_containers

        next_ids = await visible_ids(msg_containers)
        if last_message_id and last_message_id in next_ids:
            break
        if added == 0:
            # The pane moved but did not produce any new extractable message;
            # another round cannot improve the result reliably.
            break

    if last_message_id:
        cursor_index = next(
            (
                index
                for index, record in enumerate(ordered_records)
                if record.get("whatsapp_message_id") == last_message_id
            ),
            None,
        )
    else:
        cursor_index = None

    if cursor_index is not None:
        candidate_records = ordered_records[cursor_index + 1 :]
    else:
        if last_message_id:
            logger.info(
                "📍 Checkpoint %s is outside the rendered window; using only the latest %s messages",
                last_message_id,
                message_limit,
            )
        candidate_records = ordered_records

    # The cap is applied after cursor slicing so a burst between scans cannot
    # cause an unbounded OCR pass.
    candidate_records = candidate_records[-message_limit:]

    # ``ordered_records`` is oldest -> newest; item zero must remain the newest
    # high-water mark persisted by the worker.
    messages = list(reversed(candidate_records))
    logger.info("📨 Extracted %s candidate new messages", len(messages))
    return messages


async def forward_message_to_group(
    page: Page, target_group_name: str, formatted_text: str
) -> bool:
    """Navigate to the target group and send a formatted text message.

    Args:
        page: Playwright page.
        target_group_name: Name of the group to forward to.
        formatted_text: The message text to send.

    Returns:
        True if the message was sent successfully.
    """
    logger.info(f"📤 Forwarding message to group: {target_group_name}")

    # Navigate to the target group
    opened = await navigate_to_group(page, target_group_name)
    if not opened:
        logger.error(f"❌ Could not open target group: {target_group_name}")
        return False

    await asyncio.sleep(1)

    # Find the message input box
    try:
        input_box = await page.query_selector(MSG_INPUT_SELECTOR)
        if not input_box:
            # Fallback: any contenteditable in the footer
            input_boxes = await page.query_selector_all(
                '#main div[contenteditable="true"], '
                'footer div[contenteditable="true"]'
            )
            for ib in input_boxes:
                try:
                    tab_index = await ib.get_attribute("data-tab")
                    if tab_index == "10":  # message compose tab
                        input_box = ib
                        break
                except Exception:
                    continue

        if not input_box:
            logger.error("❌ Could not find message input box")
            return False

        # Click and type the message
        await input_box.click()
        await asyncio.sleep(0.3)

        # Paste the formatted text via clipboard
        await page.evaluate(
            """(text) => {
                const dataTransfer = new DataTransfer();
                dataTransfer.setData('text/plain', text);
                const event = new ClipboardEvent('paste', {
                    clipboardData: dataTransfer,
                    bubbles: true,
                    cancelable: true,
                });
                document.activeElement.dispatchEvent(event);
            }""",
            formatted_text,
        )
        await asyncio.sleep(0.5)

        # Click send button
        send_btn = await page.query_selector(SEND_BUTTON_SELECTOR)
        if send_btn:
            await send_btn.click()
        else:
            # Try pressing Enter to send
            await page.keyboard.press("Enter")

        await asyncio.sleep(2)
        logger.info(f"✅ Message forwarded to: {target_group_name}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to forward message: {e}")
        return False


async def safe_close(pw, context: BrowserContext) -> None:
    """Safely close browser resources."""
    try:
        await context.close()
    except Exception:
        pass
    try:
        await pw.stop()
    except Exception:
        pass

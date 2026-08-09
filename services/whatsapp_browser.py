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
import json
import os
import random
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
    '#pane-side, div[aria-label="Chat list"], div[role="grid"][aria-label], '
    'div[data-testid="chat-list"]'
)

# Individual chat rows inside the sidebar
CHAT_ROW_SELECTOR = 'div[role="row"], div[data-testid="cell-frame-container"]'

# Chat name/title inside a row
CHAT_NAME_SELECTOR = 'span[data-testid="cell-frame-title"], span[dir="auto"]'

# Search box for finding groups
SEARCH_BOX_SELECTOR = 'div[data-testid="chat-list-search"], div[contenteditable="true"][data-tab="3"]'

# Message container in conversation pane
MSG_CONTAINER_SELECTOR = 'div[data-testid="msg-container"], div[role="row"][data-id]'

# Message text content
MSG_TEXT_SELECTOR = 'span.selectable-text, span[data-testid="msg-text"]'

# Image inside a message
MSG_IMAGE_SELECTOR = 'img[data-testid="image-thumb"], div[data-testid="image-thumb"] img, img'

# Message input box
MSG_INPUT_SELECTOR = 'div[contenteditable="true"][data-tab="10"], div[contenteditable="true"][role="textbox"]'

# Send button
SEND_BUTTON_SELECTOR = 'button[data-testid="compose-btn-send"], span[data-testid="send"]'

# Chat header / group name in conversation
CHAT_HEADER_SELECTOR = 'div[data-testid="conversation-header"], header span[dir="auto"]'

# Loading / spinner indicators
LOADING_SELECTOR = 'div[data-testid="progress-bar"], span[data-testid="loading"]'

# Main pane (conversation area)
MAIN_PANE_SELECTOR = 'div[data-testid="conversation-panel-wrapper"], div[data-testid="conversation-panel"]'

# Candidate selectors that indicate "you are logged in" (the main interface).
# Ordered from most- to least-reliable; the first match wins.  Old data-testid
# entries are kept as fallbacks for older web builds.
LOGGED_IN_SELECTORS = (
    PANE_SIDE_SELECTOR,
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
                    return False
            return True
    except Exception:
        pass
    return False


async def wait_for_qr_scan(page: Page, max_wait_seconds: int = 120) -> bool:
    """Wait for the user to scan the QR code and WhatsApp Web to fully load.

    Returns True if login succeeded, False on timeout.
    """
    logger.info("📱 Waiting for QR code scan...")
    deadline = time.monotonic() + max_wait_seconds

    while time.monotonic() < deadline:
        try:
            if await is_logged_in(page):
                logger.info("✅ WhatsApp Web logged in — chat list detected")
                await asyncio.sleep(2)  # let the UI fully settle
                return True

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


async def scrape_messages_from_current_chat(
    page: Page, last_message_id: Optional[str] = None, last_timestamp: Optional[str] = None
) -> list[dict]:
    """Scrape new messages from the currently open chat.

    Args:
        page: Playwright page with a group conversation open.
        last_message_id: The ID of the last message we processed (for incremental scraping).
        last_timestamp: The timestamp of the last message we processed.

    Returns:
        List of message dicts with keys:
            whatsapp_message_id, sender_name, message_text, message_type,
            timestamp, raw_image_bytes
    """
    messages = []

    # Wait for messages to be visible
    try:
        await page.wait_for_selector(MSG_CONTAINER_SELECTOR, timeout=10000)
    except Exception:
        logger.warning("⚠️  No message containers found in current chat")
        return messages

    await asyncio.sleep(1)

    # Scroll up to load older messages (limited scrolls)
    for _ in range(5):
        try:
            main_pane = await page.query_selector(MAIN_PANE_SELECTOR)
            if main_pane:
                await main_pane.evaluate("el => el.scrollTop = 0")
            else:
                # Fallback: scroll the whole conversation pane
                await page.evaluate("""
                    const pane = document.querySelector('[data-testid="conversation-panel-wrapper"]')
                        || document.querySelector('[data-testid="conversation-panel"]');
                    if (pane) pane.scrollTop = 0;
                """)
            await asyncio.sleep(1.5)
        except Exception:
            break

    # Collect message containers
    msg_containers = await page.query_selector_all(MSG_CONTAINER_SELECTOR)
    logger.info(f"📨 Found {len(msg_containers)} message containers in current chat")

    found_last = False

    for container in msg_containers:
        try:
            # Extract message ID
            msg_id = await container.get_attribute("data-id")
            if not msg_id:
                msg_id = await container.get_attribute("id")

            # If we've already processed this message, skip
            if last_message_id and msg_id == last_message_id:
                found_last = True
                continue

            # If we found the last processed message earlier, this one is new
            # (messages appear in chronological order from top to bottom)

            # Extract sender name
            sender_name = None
            try:
                sender_el = await container.query_selector(
                    'span[data-testid="msg-sender"], span[aria-label]'
                )
                if sender_el:
                    sender_name = (await sender_el.inner_text()).strip()
            except Exception:
                pass

            # Determine message type: text, image, or both
            is_image = False
            try:
                img_el = await container.query_selector(MSG_IMAGE_SELECTOR)
                if img_el:
                    is_image = True
            except Exception:
                pass

            message_text = None
            raw_image_bytes = None

            if is_image:
                # Download the image
                try:
                    img_el = await container.query_selector(MSG_IMAGE_SELECTOR)
                    if img_el:
                        img_src = await img_el.get_attribute("src")
                        if img_src and img_src.startswith("blob:"):
                            # Get blob data via JS
                            raw_image_bytes = await page.evaluate(
                                """async (selector) => {
                                    const img = document.querySelector(selector);
                                    if (!img || !img.src.startsWith('blob:')) return null;
                                    const resp = await fetch(img.src);
                                    const blob = await resp.blob();
                                    return new Promise((resolve) => {
                                        const reader = new FileReader();
                                        reader.onloadend = () => resolve(reader.result);
                                        reader.readAsDataURL(blob);
                                    });
                                }""",
                                MSG_IMAGE_SELECTOR,
                            )
                        elif img_src and img_src.startswith("data:"):
                            raw_image_bytes = img_src
                        else:
                            # Try screenshotting the image element
                            raw_image_bytes = await img_el.screenshot()
                            if raw_image_bytes:
                                raw_image_bytes = base64.b64encode(raw_image_bytes).decode()
                except Exception as e:
                    logger.debug(f"Image download failed: {e}")

                # Check for caption text alongside the image
                try:
                    text_el = await container.query_selector(MSG_TEXT_SELECTOR)
                    if text_el:
                        message_text = (await text_el.inner_text()).strip()
                except Exception:
                    pass

                message_type = "image"
            else:
                # Plain text message
                try:
                    text_el = await container.query_selector(MSG_TEXT_SELECTOR)
                    if text_el:
                        message_text = (await text_el.inner_text()).strip()
                except Exception:
                    pass
                message_type = "text"

            # Extract timestamp
            timestamp = None
            try:
                time_el = await container.query_selector(
                    'span[data-testid="msg-time"], div[data-testid="msg-meta"] span, span[dir="auto"]'
                )
                if time_el:
                    timestamp = (await time_el.inner_text()).strip()
            except Exception:
                pass

            if not message_text and not raw_image_bytes:
                continue

            messages.append({
                "whatsapp_message_id": msg_id,
                "sender_name": sender_name,
                "message_text": message_text,
                "message_type": message_type,
                "timestamp": timestamp,
                "raw_image_bytes": raw_image_bytes,
            })

        except Exception as e:
            logger.debug(f"Error extracting message: {e}")
            continue

    logger.info(f"📨 Extracted {len(messages)} new messages")
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
            input_boxes = await page.query_selector_all('div[contenteditable="true"]')
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

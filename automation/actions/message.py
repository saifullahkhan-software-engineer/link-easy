"""
Action: Send a LinkedIn direct message.
FILE: automation/actions/message.py

Used for Day 3 (intro message), Day 4 (follow-up if pending),
and Day 5 (thanks message if accepted).

LinkedIn only allows messaging 1st-degree connections, so the action is
expected to fail gracefully for non-connections.  To keep it reliable we use
two paths:

1. The profile page's "Message" button — found with a *case-insensitive*
   ``aria-label`` selector and a real wait (LinkedIn lazy-renders the profile
   action buttons, so an immediate one-shot query frequently misses it).
2. Fallback to LinkedIn's direct compose URL
   ``/messaging/compose/?recipient=<profile-slug>`` which opens a compose box
   without depending on the profile DOM at all.  This still only works for
   1st-degree connections, but is far more robust to LinkedIn A/B tests and
   slow page loads.
"""
import asyncio
import random
from patchright.async_api import Page
from automation.human import (
    human_click,
    human_mouse_move,
    human_scroll,
    random_idle_pause,
    find_and_click_resilient,
)
from core.logging_config import get_logger, should_take_screenshots

logger = get_logger(__name__)

# The compose box used by both the profile message popover and the full
# messaging page.  Contenteditable is required — otherwise we can match the
# empty ghost-text container instead of the actual input.
COMPOSE_BOX_SELECTOR = "div.msg-form__contenteditable[contenteditable='true']"


def _profile_slug(profile_url: str) -> str | None:
    """Extract the public /in/<slug> identifier from a profile URL."""
    url = profile_url.strip().rstrip("/").split("?")[0]
    if "/in/" not in url:
        return None
    return url.rsplit("/", 1)[-1]


async def _find_message_button(page: Page):
    """
    Wait for and return the profile's "Message" button (1st-degree only).

    Returns None instead of raising so callers can fall back to the direct
    compose URL.  Uses a case-insensitive attribute selector because LinkedIn
    sometimes renders the aria-label as ``Message ...`` and sometimes as
    ``message``, and waits for it to become visible because the profile
    action buttons are lazy-rendered after the initial DOM load.
    """
    selectors = [
        "button[aria-label*='message' i]",          # case-insensitive aria-label
        "button.artdeco-button:has-text('Message')",
    ]
    for selector in selectors:
        try:
            # Up to ~12s so slow/lazy-loaded profiles still resolve.
            btn = await page.wait_for_selector(selector, state="visible", timeout=12000)
            if btn:
                return btn
        except Exception:
            continue
    return None


async def _open_compose_on_profile(page: Page, profile_url: str) -> bool:
    """
    Navigate to the profile and click its Message button.  Returns True if the
    compose box appeared, False if the profile isn't a 1st-degree connection
    (or the button never rendered).
    """
    await page.goto(profile_url, wait_until="domcontentloaded")
    await random_idle_pause(3, 5)

    if "/in/" not in page.url:
        logger.warning("⚠️ Not a profile page: %s", page.url)
        return False

    # Natural scroll before clicking
    await human_scroll(page)
    await random_idle_pause(1.5, 3.5)

    message_btn = await _find_message_button(page)
    if not message_btn:
        return False

    await human_click(page, message_btn)
    await random_idle_pause(1.0, 2.5)

    return await _wait_for_compose_box(page)


async def _open_compose_direct(page: Page, profile_url: str) -> bool:
    """
    Fallback: open LinkedIn's messaging compose page with the recipient
    pre-filled from the profile slug.  Avoids the profile DOM entirely.
    """
    slug = _profile_slug(profile_url)
    if not slug:
        return False

    compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={slug}"
    logger.info("💬 Opening direct compose: %s", compose_url)
    try:
        await page.goto(compose_url, wait_until="domcontentloaded")
        await random_idle_pause(2, 4)
    except Exception as e:
        logger.warning("⚠️ Failed to open direct compose: %s", e)
        return False

    return await _wait_for_compose_box(page)


async def _wait_for_compose_box(page: Page) -> bool:
    """Wait up to ~12s for the message compose box to be visible."""
    try:
        await page.wait_for_selector(
            COMPOSE_BOX_SELECTOR, state="visible", timeout=12000
        )
        return True
    except Exception:
        return False


async def _type_and_send(page: Page, message: str) -> bool:
    """Click the compose box, type the message humanly, then send it. Returns True if sent."""
    box = await page.wait_for_selector(COMPOSE_BOX_SELECTOR, state="visible", timeout=8000)
    if not box:
        logger.warning("⚠️ Message compose box not found or not visible")
        return False

    await human_click(page, COMPOSE_BOX_SELECTOR)
    await random_idle_pause(0.5, 1.2)

    # Clear any existing text (just in case)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await random_idle_pause(0.2, 0.5)

    # Type character by character with human speed
    for char in message:
        await page.keyboard.type(char)
        delay = random.uniform(0.03, 0.15)
        if char in " .,!?":
            delay = random.uniform(0.08, 0.22)
        await asyncio.sleep(delay)

    await random_idle_pause(1.5, 3.0)

    # Try to find and click the send button
    send_selectors = [
        "button.msg-form__send-button",
        "button.msg-form__send-btn",
        ".msg-form__footer button[type='submit']",
        "button:has-text('Send')",
        "button[aria-label*='Send' i]",
    ]

    sent = False
    try:
        # Use a resilient search for the send button
        await find_and_click_resilient(page, send_selectors, "Send Message button")
        sent = True
        logger.info("✅ Clicked Send button")
    except Exception as e:
        logger.warning("⚠️ Could not find or click Send button, trying Enter fallback: %s", e)
        # Fallback to Control+Enter which is more reliable than just Enter
        await page.keyboard.press("Control+Enter")
        await asyncio.sleep(1)
        # Also try plain Enter just in case
        await page.keyboard.press("Enter")
        sent = True # Assume it worked if no exception

    # Verification: check if the box is cleared
    await asyncio.sleep(2)
    try:
        content = await page.inner_text(COMPOSE_BOX_SELECTOR)
        if content.strip() != "":
            logger.warning("⚠️ Compose box still contains text after send attempt: %r", content.strip())
            # If text remains, maybe it didn't send. Try one last Enter.
            await page.keyboard.press("Control+Enter")
            await asyncio.sleep(1)
            content = await page.inner_text(COMPOSE_BOX_SELECTOR)
            if content.strip() != "":
                 logger.error("❌ Message failed to send — text still in box")
                 return False
    except Exception as e:
        logger.debug("Failed to verify box content (might be closed): %s", e)
        # If box is gone, that's usually a good sign
        pass

    return sent


async def send_message(page: Page, profile_url: str,
                        message_text: str,
                        first_name: str = None) -> dict:
    """
    Sends a direct message to a LinkedIn profile.
    The Message button is only available for 1st-degree connections.
    For non-connections this will fail gracefully.
    """
    result = {"sent": False, "error": None}

    # Substitute template placeholders
    message = message_text.replace("{{first_name}}", first_name or "there")

    try:
        # Path 1: profile page's Message button (1st-degree connections only).
        compose_opened = await _open_compose_on_profile(page, profile_url)

        # Path 2: direct compose URL fallback — covers slow/lazy-rendered
        # profiles and LinkedIn A/B layout changes.
        if not compose_opened:
            logger.info("🔄 Message button not on profile, trying direct compose URL...")
            compose_opened = await _open_compose_direct(page, profile_url)

        if not compose_opened:
            if should_take_screenshots():
                try:
                    await page.screenshot(
                        path="message_button_missing_debug.png", full_page=True
                    )
                except Exception:
                    pass
            result["error"] = (
                "Message compose box not found — the lead may not be a "
                "1st-degree connection yet (or the profile failed to load)."
            )
            return result

        result["sent"] = await _type_and_send(page, message)
        if not result["sent"]:
            result["error"] = "Failed to send message: compose box did not clear after sending."

    except Exception as e:
        result["error"] = str(e)

    return result

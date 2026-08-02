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


async def _find_visible_send_button(page: Page):
    """Return the enabled Send button belonging to the active message form.

    LinkedIn can keep hidden compose forms and unrelated ``Send`` buttons in
    the DOM.  A page-wide text selector can therefore report a click although
    it never submits the message being composed.
    """
    selectors = [
        "form.msg-form button.msg-form__send-button:not([disabled])",
        "form.msg-form button.msg-form__send-btn:not([disabled])",
        ".msg-form__footer button[type='submit']:not([disabled])",
    ]
    for selector in selectors:
        try:
            button = await page.wait_for_selector(selector, state="visible", timeout=3000)
            if button and await button.is_enabled():
                return button
        except Exception:
            continue
    return None


async def _compose_box_cleared(page: Page, timeout_seconds: float = 8) -> bool:
    """Wait for LinkedIn to accept the submission and clear the draft."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            box = await page.query_selector(COMPOSE_BOX_SELECTOR)
            if box is None or not await box.is_visible():
                # LinkedIn sometimes closes the inline composer after sending.
                return True
            if not (await box.inner_text()).strip():
                return True
        except Exception:
            # A composer removed after submit is a positive confirmation.
            return True
        await asyncio.sleep(0.4)
    return False


async def _type_and_send(page: Page, message: str) -> bool:
    """Type and submit a message, returning True only after UI confirmation.

    We intentionally do not use Enter as a fallback.  LinkedIn's "press Enter
    to send" preference is account/UI dependent; when disabled it merely adds
    a newline.  Clicking the enabled Send button works independently of that
    preference and is the only path counted as a sent message.
    """
    if not message or not message.strip():
        logger.warning("⚠️ Refusing to send an empty LinkedIn message")
        return False

    box = await page.wait_for_selector(COMPOSE_BOX_SELECTOR, state="visible", timeout=8000)
    if not box:
        logger.warning("⚠️ Message compose box not found or not visible")
        return False

    await human_click(page, box)
    await random_idle_pause(0.5, 1.2)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await random_idle_pause(0.2, 0.5)

    for char in message:
        await page.keyboard.type(char)
        delay = random.uniform(0.03, 0.15)
        if char in " .,!?":
            delay = random.uniform(0.08, 0.22)
        await asyncio.sleep(delay)

    await random_idle_pause(1.5, 3.0)
    send_button = await _find_visible_send_button(page)
    if not send_button:
        logger.error("❌ Enabled Send button not found; message was not submitted")
        return False

    try:
        await human_click(page, send_button)
    except Exception as exc:
        logger.error("❌ Failed to click the active Send button: %s", exc)
        return False

    if await _compose_box_cleared(page):
        logger.info("✅ LinkedIn accepted the message submission (composer cleared)")
        return True

    logger.error("❌ LinkedIn did not confirm sending; draft text remained in the composer")
    return False


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

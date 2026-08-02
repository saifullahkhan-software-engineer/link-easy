"""
Action: Send a LinkedIn connection request with optional personalised note.
FILE: automation/actions/connect.py

Day 2 of the drip sequence. Uses {{first_name}} placeholder substitution
from the campaign's connection_note_template.

Like the message action, ``sent`` is only True when the request was
*positively* confirmed — the invitation modal closed and the profile now
shows a Pending state (or LinkedIn raised no error).  The previous version
reported success purely because a "Send" element existed and got clicked,
which silently counted weekly-invite-limit blocks, unfilled notes and
already-pending profiles as successes.
"""
import asyncio
import random
from patchright.async_api import Page
from automation.human import human_click, human_type, human_scroll, random_idle_pause
from core.logging_config import get_logger, should_take_screenshots

logger = get_logger(__name__)

# LinkedIn caps personalised invitation notes.
MAX_NOTE_LENGTH = 300

# "Connect" in the top-card action row.  Case-insensitive because LinkedIn
# renders both `Connect` and `Invite <name> to connect`.
CONNECT_BUTTON_SELECTORS = [
    "button[aria-label*='Invite' i][aria-label*='connect' i]",
    "button[aria-label*='Connect' i]",
    "main button.artdeco-button:has-text('Connect')",
]

# "Connect" hidden inside the More actions overflow menu.
MORE_BUTTON_SELECTORS = [
    "button[aria-label='More actions']",
    "button[aria-label*='More actions' i]",
    "main button.artdeco-dropdown__trigger:has-text('More')",
]
OVERFLOW_CONNECT_SELECTORS = [
    "div[aria-label*='Invite' i][aria-label*='connect' i]",
    "div[aria-label*='Connect' i]",
    ".artdeco-dropdown__content li:has-text('Connect')",
]

ADD_NOTE_SELECTORS = [
    "button[aria-label='Add a note']",
    "button[aria-label*='Add a note' i]",
    "button:has-text('Add a note')",
]
NOTE_BOX_SELECTORS = [
    "textarea[name='message']",
    "textarea#custom-message",
    ".send-invite__custom-message",
]
SEND_BUTTON_SELECTORS = [
    "button[aria-label='Send now']",
    "button[aria-label='Send invitation']",
    "button[aria-label*='Send' i]",
    ".artdeco-modal button:has-text('Send')",
]

MODAL_SELECTOR = ".artdeco-modal, div[role='dialog']"

# States that mean "no invitation can be sent", not "the action failed".
ALREADY_CONNECTED_HINTS = ("pending", "message", "withdraw", "following")

# LinkedIn's invite throttling / rejection copy.
BLOCKED_HINTS = (
    "you've reached the weekly invitation limit",
    "weekly invitation limit",
    "no longer accept invitations",
    "can't send an invitation",
    "cannot send an invitation",
    "try again later",
    "something went wrong",
)


def _normalize(text: str | None) -> str:
    return " ".join((text or "").split())


async def _first_visible(page: Page, selectors: list[str], timeout_ms: int = 4000):
    """Return the first visible element matching any selector, else None."""
    for selector in selectors:
        try:
            element = await page.wait_for_selector(selector, state="visible", timeout=timeout_ms)
        except Exception:
            continue
        if not element:
            continue
        try:
            if await element.is_enabled():
                return element
        except Exception:
            return element
    return None


async def _page_state_text(page: Page) -> str:
    """Lowercased text of the profile's top-card action row (for state checks)."""
    for selector in [".pv-top-card-v2-ctas", ".ph5.pb5", "main"]:
        try:
            node = await page.query_selector(selector)
            if node:
                return _normalize(await node.inner_text()).lower()
        except Exception:
            continue
    return ""


async def _blocked_reason(page: Page) -> str | None:
    """Return LinkedIn's own rejection text if the invite was refused."""
    for selector in [
        "div[data-test-artdeco-toast-item-type='error']",
        ".artdeco-toast-item--error",
        ".artdeco-modal",
        "div[role='alert']",
    ]:
        try:
            nodes = await page.query_selector_all(selector)
        except Exception:
            continue
        for node in nodes:
            try:
                if not await node.is_visible():
                    continue
                text = _normalize(await node.inner_text())
            except Exception:
                continue
            lowered = text.lower()
            for hint in BLOCKED_HINTS:
                if hint in lowered:
                    return text[:240]
    return None


async def _modal_closed(page: Page, timeout_seconds: float = 8.0) -> bool:
    """Wait for the invitation modal to disappear after clicking Send."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            modals = await page.query_selector_all(MODAL_SELECTOR)
            if not any([await m.is_visible() for m in modals]):
                return True
        except Exception:
            return True
        await asyncio.sleep(0.4)
    return False


async def _invite_confirmed(page: Page, timeout_seconds: float = 8.0) -> bool:
    """Positive confirmation: the profile now shows a Pending/Withdraw state."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            pending = await page.query_selector(
                "button[aria-label*='Pending' i], button[aria-label*='Withdraw' i]"
            )
            if pending and await pending.is_visible():
                return True
        except Exception:
            pass
        state = await _page_state_text(page)
        if "pending" in state or "invitation sent" in state:
            return True
        await asyncio.sleep(0.5)
    return False


async def _open_connect_dialog(page: Page) -> tuple[bool, str | None]:
    """Click Connect (top card, else More menu).  Returns ``(opened, error)``."""
    connect_btn = await _first_visible(page, CONNECT_BUTTON_SELECTORS, timeout_ms=6000)

    if not connect_btn:
        logger.info("🔎 Connect not in the top card; checking the More actions menu")
        more_btn = await _first_visible(page, MORE_BUTTON_SELECTORS, timeout_ms=4000)
        if more_btn:
            await human_click(page, more_btn)
            await random_idle_pause(0.8, 1.8)
            connect_btn = await _first_visible(page, OVERFLOW_CONNECT_SELECTORS, timeout_ms=4000)

    if not connect_btn:
        state = await _page_state_text(page)
        if any(hint in state for hint in ALREADY_CONNECTED_HINTS):
            return False, (
                "Connect button not available — the lead is already connected, "
                "or an invitation is already pending."
            )
        return False, "Connect button not found on the profile."

    await human_click(page, connect_btn)
    await random_idle_pause(1.0, 2.5)
    return True, None


async def send_connection_request(page: Page, profile_url: str,
                                   first_name: str = None,
                                   note_template: str = None) -> dict:
    """
    Sends a connection request to the LinkedIn profile at profile_url.

    note_template: string with optional {{first_name}} placeholder.
    If None, sends without a note (higher acceptance rate for cold outreach).

    ``sent`` is True only when LinkedIn confirmed the invitation; ``error``
    explains every other outcome (already connected, weekly limit reached,
    note never landed, modal never closed).
    """
    result = {"sent": False, "with_note": False, "error": None}

    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(3, 6)

        if "/in/" not in page.url:
            result["error"] = f"Not a profile page: {page.url}"
            return result

        # Already connected / already pending — never a "failure", but never
        # a send either.  Detect it up front so it isn't retried forever.
        if await _invite_confirmed(page, timeout_seconds=1.5):
            result["error"] = "An invitation is already pending for this lead."
            return result

        # Scroll a bit before clicking connect (natural behaviour)
        await human_scroll(page)
        await random_idle_pause(1.5, 4.0)

        opened, open_error = await _open_connect_dialog(page)
        if not opened:
            result["error"] = open_error
            return result

        blocked = await _blocked_reason(page)
        if blocked:
            logger.error("❌ LinkedIn refused the invitation: %s", blocked)
            result["error"] = f"LinkedIn refused the invitation: {blocked}"
            return result

        # ── Add a personalised note if template is provided ───────────────────
        if note_template:
            note = note_template.replace("{{first_name}}", first_name or "there").strip()
            if len(note) > MAX_NOTE_LENGTH:
                logger.warning(
                    "⚠️ Connection note is %d chars; truncating to LinkedIn's %d-char limit",
                    len(note), MAX_NOTE_LENGTH,
                )
                note = note[:MAX_NOTE_LENGTH].rstrip()

            add_note_btn = await _first_visible(page, ADD_NOTE_SELECTORS, timeout_ms=3000)
            if not add_note_btn:
                # Free accounts lose "Add a note" once the monthly note quota
                # is spent.  Sending without a note is still worthwhile.
                logger.warning("⚠️ 'Add a note' unavailable; sending the invitation without a note")
            elif note:
                await human_click(page, add_note_btn)
                await random_idle_pause(0.8, 1.8)

                note_box = await _first_visible(page, NOTE_BOX_SELECTORS, timeout_ms=4000)
                if not note_box:
                    logger.warning("⚠️ Note textarea never appeared; sending without a note")
                else:
                    await human_type(page, note_box, note)
                    await random_idle_pause(0.5, 1.5)
                    # Verify the note actually landed before submitting.
                    try:
                        typed = _normalize(await note_box.input_value())
                    except Exception:
                        typed = ""
                    if not typed:
                        # Same failure mode as the message composer: the click
                        # was intercepted and the keystrokes went nowhere.
                        try:
                            await note_box.fill(note)
                            await asyncio.sleep(0.5)
                            typed = _normalize(await note_box.input_value())
                        except Exception:
                            typed = ""
                    if typed:
                        result["with_note"] = True
                    else:
                        logger.warning("⚠️ Note text never landed in the textarea; sending without a note")

        # Click Send
        send_btn = await _first_visible(page, SEND_BUTTON_SELECTORS, timeout_ms=5000)
        if not send_btn:
            if should_take_screenshots():
                try:
                    await page.screenshot(path="connect_send_missing_debug.png", full_page=True)
                except Exception:
                    pass
            result["error"] = "Send button not found in the invitation dialog; nothing was submitted."
            return result

        await human_click(page, send_btn)
        await random_idle_pause(2, 4)

        blocked = await _blocked_reason(page)
        if blocked:
            logger.error("❌ LinkedIn rejected the invitation: %s", blocked)
            result["error"] = f"LinkedIn rejected the invitation: {blocked}"
            return result

        if await _invite_confirmed(page):
            logger.info("✅ Invitation confirmed (profile now shows Pending)")
            result["sent"] = True
            return result

        # The Pending badge is not always re-rendered without a reload, so a
        # cleanly closed modal with no error is accepted as sent.
        if await _modal_closed(page):
            logger.info("✅ Invitation dialog closed with no error; treating as sent")
            result["sent"] = True
            return result

        if should_take_screenshots():
            try:
                await page.screenshot(path="connect_unconfirmed_debug.png", full_page=True)
            except Exception:
                pass
        result["error"] = "LinkedIn did not confirm the invitation; the dialog stayed open."

    except Exception as e:
        result["error"] = str(e)

    return result

"""
Action: Send a LinkedIn direct message.
FILE: automation/actions/message.py

Used for Day 3 (intro message), Day 4 (follow-up if pending),
and Day 5 (thanks message if accepted).

LinkedIn only allows messaging 1st-degree connections, so the action is
expected to fail gracefully for non-connections.  To keep it reliable we use
two paths to open a composer:

1. The profile page's "Message" button — found with a *case-insensitive*
   ``aria-label`` selector and a real wait (LinkedIn lazy-renders the profile
   action buttons, so an immediate one-shot query frequently misses it).
2. Fallback to LinkedIn's direct compose URL
   ``/messaging/compose/?recipient=<profile-slug>`` which opens a compose box
   without depending on the profile DOM at all.  This still only works for
   1st-degree connections, but is far more robust to LinkedIn A/B tests and
   slow page loads.

Send confirmation is *positive*, never inferred.  An earlier version treated
"the composer cleared/closed" as proof of delivery, but the composer also
clears when the Send click lands on close/discard after a layout shift, when
the typed text never actually landed in the box, or when LinkedIn rejects the
submission (rate limit, spam guard, recipient not reachable).  We therefore:

  * read the typed draft back before submitting and refuse to click Send if
    the text never landed in the composer,
  * click the Send button that belongs to the *same* composer we typed into,
  * confirm a new bubble containing our message appeared in the conversation
    (falling back to the full messaging thread when the composer closes), and
  * surface LinkedIn's own error banners instead of reporting a false success.
"""
import asyncio
import random
from patchright.async_api import Page
from automation.human import (
    human_click,
    human_scroll,
    random_idle_pause,
)
from core.logging_config import get_logger, should_take_screenshots

logger = get_logger(__name__)

# The compose box used by both the profile message popover and the full
# messaging page.  Contenteditable is required — otherwise we can match the
# empty ghost-text container instead of the actual input.
COMPOSE_BOX_SELECTOR = "div.msg-form__contenteditable[contenteditable='true']"

# A single rendered message in a conversation.  Both the mini messaging
# overlay on the profile page and the full messaging page render conversation
# entries with this class, and a freshly sent message appears here immediately.
SENT_EVENT_SELECTOR = "li.msg-s-message-list__event"

# Surfaces LinkedIn uses when a submission is rejected (toast + inline forms).
ERROR_SURFACE_SELECTORS = [
    "div[data-test-artdeco-toast-item-type='error']",
    ".artdeco-toast-item--error",
    ".msg-form__error",
]
ERROR_TEXT_HINTS = (
    "couldn't send", "could not send", "wasn't sent", "was not sent",
    "not be delivered", "limit reached", "try again",
)

# Send-button variants, in preference order.  These are intentionally free of
# visibility/disabled clauses so the same list works for both page-wide and
# composer-scoped lookups; visibility and enabled state are checked on the
# resolved handle instead.
SEND_BUTTON_SELECTORS = [
    "button.msg-form__send-button",
    "button.msg-form__send-btn",
    ".msg-form__footer button[type='submit']",
    "button[aria-label*='Send' i]",
    "button:has-text('Send')",
]


def _profile_slug(profile_url: str) -> str | None:
    """Extract the public /in/<slug> identifier from a profile URL."""
    url = profile_url.strip().rstrip("/").split("?")[0]
    if "/in/" not in url:
        return None
    return url.rsplit("/", 1)[-1]


def _normalize(text: str | None) -> str:
    """Collapse all whitespace so DOM and template text compare reliably."""
    return " ".join((text or "").split())


def _message_tail(message: str, length: int = 40) -> str:
    """The distinctive suffix we look for in the conversation after sending."""
    return _normalize(message)[-length:]


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


async def _resolve_compose_box(page: Page, timeout_ms: int = 8000):
    """Return the composer we should actually type into.

    ``query_selector`` returns the *first* match, but LinkedIn frequently keeps
    stale/minimised message overlays mounted, so the first contenteditable can
    be an invisible bubble while the real (just opened) composer is the last
    one.  Typing into the wrong one is exactly how the draft "disappears".
    """
    try:
        await page.wait_for_selector(COMPOSE_BOX_SELECTOR, state="visible", timeout=timeout_ms)
    except Exception:
        return None
    try:
        boxes = await page.query_selector_all(COMPOSE_BOX_SELECTOR)
    except Exception:
        boxes = []
    visible = []
    for box in boxes:
        try:
            if await box.is_visible() and await box.bounding_box():
                visible.append(box)
        except Exception:
            continue
    if not visible:
        return None
    return visible[-1]


async def _focus_compose_box(page: Page, box) -> bool:
    """Put the caret inside ``box``, verifying focus actually landed there.

    ``human_click`` clicks raw viewport coordinates, which LinkedIn's hover
    cards, sticky headers and overlay chrome routinely intercept — the click
    is swallowed, focus never moves, and every keystroke is then typed into
    nothing.  We therefore verify ``document.activeElement`` and escalate:
    human click → element click → JS focus + caret placement.
    """
    async def _focused() -> bool:
        try:
            return bool(await box.evaluate(
                "el => el === document.activeElement || el.contains(document.activeElement)"
            ))
        except Exception:
            return False

    try:
        await human_click(page, box)
        await random_idle_pause(0.3, 0.8)
    except Exception as exc:
        logger.debug("🔎 Human click on the composer failed: %s", exc)
    if await _focused():
        return True

    logger.debug("🔎 Composer not focused after human click; retrying with a direct click")
    try:
        await box.click(timeout=3000)
        await asyncio.sleep(0.4)
    except Exception as exc:
        logger.debug("🔎 Direct click on the composer failed: %s", exc)
    if await _focused():
        return True

    logger.debug("🔎 Composer still not focused; forcing focus via JS caret placement")
    try:
        await box.evaluate(
            """el => {
                el.focus();
                const range = document.createRange();
                range.selectNodeContents(el);
                range.collapse(false);
                const sel = window.getSelection();
                sel.removeAllRanges();
                sel.addRange(range);
            }"""
        )
        await asyncio.sleep(0.3)
    except Exception as exc:
        logger.debug("🔎 JS focus of the composer failed: %s", exc)
    return await _focused()


async def _box_text(box) -> str:
    """Read the composer's current draft text."""
    try:
        return _normalize(await box.evaluate("el => el.innerText"))
    except Exception:
        pass
    try:
        return _normalize(await box.inner_text())
    except Exception:
        return ""


async def _type_into_compose_box(page: Page, box, message: str, tail: str) -> str:
    """Type ``message`` into ``box``, escalating until the text lands.

    Returns the draft text finally present in the composer ("" if every
    strategy failed).  Character-by-character keyboard typing stays the
    preferred (most human) path; the fallbacks only run when the composer is
    still empty, which is otherwise a guaranteed dead end.
    """
    # Clear whatever is there (previous draft / ghost content).
    try:
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
    except Exception:
        pass
    await random_idle_pause(0.2, 0.5)

    for char in message:
        await page.keyboard.type(char)
        delay = random.uniform(0.03, 0.15)
        if char in " .,!?":
            delay = random.uniform(0.08, 0.22)
        await asyncio.sleep(delay)

    await random_idle_pause(1.0, 2.0)

    typed = await _box_text(box)
    if typed and tail in typed:
        return typed

    # Fallback 1: re-focus and insert the text in one shot.  This survives the
    # case where focus was stolen mid-typing (LinkedIn re-renders the form).
    logger.warning("⚠️ Keystrokes did not land in the composer (found %r); retrying with insert_text", typed[:80])
    if await _focus_compose_box(page, box):
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.keyboard.insert_text(message)
            await asyncio.sleep(1.0)
        except Exception as exc:
            logger.debug("🔎 insert_text fallback failed: %s", exc)
        typed = await _box_text(box)
        if typed and tail in typed:
            logger.info("✅ Draft landed in the composer via insert_text fallback")
            return typed

    # Fallback 2: write straight into the DOM and fire the input events
    # LinkedIn's editor listens for, so the Send button becomes enabled.
    logger.warning("⚠️ insert_text fallback did not land either; writing the draft via the DOM")
    try:
        await box.evaluate(
            """(el, text) => {
                el.focus();
                el.innerHTML = '';
                const p = document.createElement('p');
                p.textContent = text;
                el.appendChild(p);
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true, cancelable: true, inputType: 'insertText', data: text,
                }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'a' }));
            }""",
            message,
        )
        await asyncio.sleep(1.0)
    except Exception as exc:
        logger.debug("🔎 DOM insertion fallback failed: %s", exc)

    typed = await _box_text(box)
    if typed and tail in typed:
        logger.info("✅ Draft landed in the composer via the DOM fallback")
    return typed


async def _pick_enabled_send_button(scope, timeout_ms: int = 2500):
    """Return the first visible, enabled Send button found under ``scope``.

    ``scope`` may be a Page or an ElementHandle (a specific composer form);
    ElementHandle lookups are scoped relative to the element, so the button is
    guaranteed to belong to that composer rather than any other ``msg-form``
    on the page.
    """
    for selector in SEND_BUTTON_SELECTORS:
        try:
            button = await scope.wait_for_selector(
                selector, state="visible", timeout=timeout_ms
            )
        except Exception:
            button = None
        if not button:
            continue
        try:
            if await button.is_enabled():
                return button
        except Exception:
            continue
    return None


async def _send_button_for_box(page: Page, box):
    """Return the Send button belonging to the composer that contains ``box``.

    Anchoring the button to the typed composer's own ``.msg-form`` prevents a
    mismatch where we type into one composer but click (and verify against)
    another composer on the same page.  Falls back to a page-wide search only
    if the form ancestor cannot be resolved.
    """
    form = None
    try:
        form = await box.evaluate_handle("el => el.closest('.msg-form')")
    except Exception:
        form = None
    if form:
        try:
            button = await _pick_enabled_send_button(form)
            if button:
                return button
        except Exception:
            pass
        logger.debug("🔎 Send button not found inside the typed composer; falling back to page-wide search")
    return await _pick_enabled_send_button(page)


async def _compose_box_cleared(box, timeout_seconds: float = 8) -> bool:
    """Wait for the *same* composer used for typing to clear or disappear.

    This is only a *gate* for deeper verification — a cleared composer on its
    own is NOT proof of delivery (the draft may have been discarded or the
    submission rejected), so callers must still confirm the message bubble.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        try:
            if not await box.is_visible():
                # LinkedIn sometimes closes the inline composer after sending.
                return True
            if not (await box.inner_text()).strip():
                return True
        except Exception:
            # The typed composer was removed after submit.
            return True
        await asyncio.sleep(0.4)
    return False


async def _count_matching_events(page: Page, tail: str) -> int:
    """How many visible conversation bubbles currently contain ``tail``."""
    if not tail:
        return 0
    try:
        events = await page.query_selector_all(SENT_EVENT_SELECTOR)
    except Exception:
        return 0
    matches = 0
    for event in events[-12:]:
        try:
            if tail in _normalize(await event.inner_text()):
                matches += 1
        except Exception:
            continue
    return matches


async def _wait_for_new_bubble(page: Page, tail: str, previous_count: int,
                               timeout_seconds: float = 8.0) -> bool:
    """Wait until the conversation shows a *new* bubble containing our text.

    Comparing against the pre-send count keeps this truthful even when the
    exact same message was sent to this recipient on a previous run.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        if await _count_matching_events(page, tail) > previous_count:
            return True
        await asyncio.sleep(0.4)
    return False


async def _error_banner_text(page: Page) -> str | None:
    """Return LinkedIn's own failure text, if it rejected the submission."""
    for selector in ERROR_SURFACE_SELECTORS:
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
            if text:
                return text[:240]
    # Last resort: an inline banner inside any visible message form.
    try:
        forms = await page.query_selector_all(".msg-form")
    except Exception:
        forms = []
    for form in forms:
        try:
            if not await form.is_visible():
                continue
            text = _normalize(await form.inner_text())
        except Exception:
            continue
        lowered = text.lower()
        if any(hint in lowered for hint in ERROR_TEXT_HINTS):
            return text[:240]
    return None


async def _verify_delivery_in_thread(page: Page, profile_url: str, tail: str) -> bool:
    """Fallback delivery check on the full messaging thread.

    Used when the inline composer closed after the click (so the overlay's
    event list is gone).  After a real send, the direct compose URL redirects
    to the existing conversation, whose event list contains our bubble.  If
    LinkedIn discarded the submission, the thread never shows the text.
    """
    slug = _profile_slug(profile_url)
    if not slug:
        return False
    try:
        await page.goto(
            f"https://www.linkedin.com/messaging/compose/?recipient={slug}",
            wait_until="domcontentloaded",
        )
        await random_idle_pause(2, 4)
    except Exception as e:
        logger.warning("⚠️ Could not open messaging thread for delivery verification: %s", e)
        return False
    try:
        await page.wait_for_selector(SENT_EVENT_SELECTOR, state="visible", timeout=8000)
    except Exception:
        return False
    return await _count_matching_events(page, tail) > 0


async def _type_and_send(page: Page, message: str,
                         profile_url: str) -> tuple[bool, str | None]:
    """Type and submit a message, returning ``(sent, error)``.

    Success requires *positive* confirmation that the message reached the
    conversation — never the mere disappearance of the composer.  We
    intentionally do not use Enter as a fallback: LinkedIn's "press Enter to
    send" preference is account/UI dependent; when disabled it merely adds a
    newline.  Clicking the enabled Send button works independently of that
    preference and is the only path counted as a sent message.
    """
    if not message or not message.strip():
        logger.warning("⚠️ Refusing to send an empty LinkedIn message")
        return False, "Refusing to send an empty LinkedIn message."

    box = await _resolve_compose_box(page)
    if not box:
        logger.warning("⚠️ Message compose box not found or not visible")
        return False, "Message compose box not found or not visible."

    try:
        await box.scroll_into_view_if_needed()
    except Exception:
        pass

    tail = _message_tail(message)
    # Baseline used to prove a *new* bubble appears after we click Send.
    bubbles_before = await _count_matching_events(page, tail)

    if not await _focus_compose_box(page, box):
        logger.warning("⚠️ Could not focus the composer; typing may not land")

    typed = await _type_into_compose_box(page, box, message, tail)

    # Read the draft back.  If focus was lost (or the click into the box was
    # intercepted) the keystrokes landed nowhere; the old flow still reported
    # success afterwards because the empty composer looked "cleared".
    if not typed or tail not in typed:
        logger.error("❌ Typed text did not land in the composer (found %r)", typed[:80])
        if should_take_screenshots():
            try:
                await page.screenshot(path="message_compose_empty_debug.png", full_page=True)
            except Exception:
                pass
        return False, "Typed message never appeared in the composer; nothing was submitted."

    send_button = await _send_button_for_box(page, box)
    if not send_button:
        # A disabled button means LinkedIn did not register the draft (or the
        # UI changed); it is not evidence that a message was sent.
        logger.error("❌ Enabled Send button not found; message was not submitted")
        return False, "Enabled Send button not found; message was not submitted."

    try:
        await send_button.scroll_into_view_if_needed()
    except Exception:
        pass

    try:
        await human_click(page, send_button)
    except Exception as exc:
        logger.error("❌ Failed to click the active Send button: %s", exc)
        return False, f"Failed to click the Send button: {exc}"

    # Confirmation path 1: a new bubble with our text appears in the live
    # conversation (mini overlay or full messaging page).
    if await _wait_for_new_bubble(page, tail, bubbles_before):
        logger.info("✅ Delivery confirmed: message bubble appeared in the conversation")
        return True, None

    # LinkedIn's own error surface beats any inference.
    error_text = await _error_banner_text(page)
    if error_text:
        logger.error("❌ LinkedIn rejected the message: %s", error_text)
        return False, f"LinkedIn rejected the message: {error_text}"

    # Confirmation path 2: the composer closed on submit, so verify the
    # message exists in the full messaging thread.  A cleared composer alone
    # is never enough — the draft may have been discarded.
    if await _compose_box_cleared(box, timeout_seconds=4):
        logger.info("🔎 Composer closed; verifying delivery in the messaging thread...")
        if await _verify_delivery_in_thread(page, profile_url, tail):
            logger.info("✅ Delivery confirmed in the messaging thread")
            return True, None
        return False, (
            "Composer cleared but the message was not found in the conversation "
            "thread; LinkedIn likely discarded the submission."
        )

    logger.error("❌ LinkedIn did not confirm sending; draft text remained in the composer")
    return False, "LinkedIn did not confirm sending; the draft stayed in the composer."


async def send_message(page: Page, profile_url: str,
                        message_text: str,
                        first_name: str = None) -> dict:
    """
    Sends a direct message to a LinkedIn profile.
    The Message button is only available for 1st-degree connections.
    For non-connections this will fail gracefully.

    ``sent`` is True only when delivery was positively confirmed (the message
    was seen in the conversation); otherwise ``error`` explains why LinkedIn
    could not confirm it.
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

        result["sent"], send_error = await _type_and_send(page, message, profile_url)
        if not result["sent"]:
            if should_take_screenshots():
                try:
                    await page.screenshot(
                        path="message_send_unconfirmed_debug.png", full_page=True
                    )
                except Exception:
                    pass
            result["error"] = (
                send_error
                or "Failed to send message: LinkedIn did not confirm delivery."
            )

    except Exception as e:
        result["error"] = str(e)

    return result

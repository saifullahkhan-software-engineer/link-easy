"""
Human-mimicking mouse, keyboard, and scroll behaviour.
FILE: automation/human.py
 
All Playwright interactions in NexusFlow must go through these functions.
Never call page.click() directly — always use human_click().
"""
import asyncio
import math
import logging
import random
from patchright.async_api import Page, Locator, ElementHandle
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)
 
 
async def human_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """
    Moves the mouse from its current position to (target_x, target_y)
    using a quadratic bezier curve with ease-in/ease-out speed and
    random micro-jitter to simulate hand tremor.
    """
    # Start position: approximate current location with slight randomness
    current_x = random.uniform(200, 900)
    current_y = random.uniform(150, 600)
 
    # Bezier control point creates a natural curved path
    ctrl_x = (current_x + target_x) / 2 + random.uniform(-100, 100)
    ctrl_y = (current_y + target_y) / 2 + random.uniform(-100, 100)
 
    steps = random.randint(25, 50)
 
    for i in range(steps + 1):
        t = i / steps
        # Ease-in/ease-out: t*t*(3 - 2*t) maps 0→0 and 1→1 with smooth acceleration
        eased = t * t * (3 - 2 * t)
 
        # Quadratic bezier position
        x = (1 - eased)**2 * current_x + 2 * (1 - eased) * eased * ctrl_x + eased**2 * target_x
        y = (1 - eased)**2 * current_y + 2 * (1 - eased) * eased * ctrl_y + eased**2 * target_y
 
        # Add micro-jitter (simulates natural hand tremor)
        x += random.uniform(-1.5, 1.5)
        y += random.uniform(-1.5, 1.5)
 
        await page.mouse.move(x, y)
 
        # Speed: slow at start/end, fast in middle
        speed_factor = math.sin(t * math.pi)
        delay_ms = random.uniform(8, 30) * (1 - speed_factor * 0.65)
        await asyncio.sleep(delay_ms / 1000)
 
 
async def human_click(page: Page, target: str | Locator | ElementHandle) -> None:
    """
    Moves the mouse to the element and clicks at a slightly random
    position within the element bounds (not always dead-centre).
    Accepts selector string, Locator, or ElementHandle.
    """
    element = None
    
    # Handle different input types
    if isinstance(target, str):
        try:
            element = await page.wait_for_selector(target, timeout=5000)
        except Exception as e:
            # On failure, save a screenshot for debugging.
            # Gate this on dev mode: the resilient fallback loops below call
            # human_click() through human_type() for EVERY non-matching
            # selector candidate (~30x per LinkedIn login attempt), and an
            # unconditional full-page screenshot per miss both wasted disk
            # and added seconds to an already slow flow in production.
            screenshot_note = ""
            if should_take_screenshots():
                try:
                    screenshot_path = f"error_screenshot_{random.randint(1000, 9999)}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    screenshot_note = f"Screenshot saved to '{screenshot_path}'. "
                except Exception:
                    pass
            # Re-raise the exception with more context
            raise type(e)(
                f"Failed to find selector '{target}'. "
                f"{screenshot_note}"
                f"Current URL: {page.url}. Original error: {e}"
            ) from e
    elif isinstance(target, Locator):
        element = await target.element_handle()
    elif isinstance(target, ElementHandle):
        element = target
    else:
        raise TypeError(f"human_click() expects str, Locator, or ElementHandle, got {type(target)}")

    # Elements below the fold have viewport coordinates that point at whatever
    # is currently painted there, so the click lands on the wrong node.
    try:
        await element.scroll_into_view_if_needed(timeout=3000)
        await asyncio.sleep(random.uniform(0.15, 0.4))
    except Exception:
        pass

    box = await element.bounding_box()
    if not box:
        # Fallback for elements without a bounding box (e.g., some SVGs)
        await element.click()
        return

    # An element scrolled under a sticky header/footer, or sized 0, cannot be
    # clicked by coordinates at all.
    viewport = page.viewport_size or {"width": 1440, "height": 900}
    if (box["width"] <= 0 or box["height"] <= 0
            or box["y"] + box["height"] < 0 or box["y"] > viewport["height"]):
        await element.click()
        await asyncio.sleep(random.uniform(0.2, 0.6))
        return

    # Click slightly off-centre — humans don't click exact pixel-perfect centres
    target_x = box["x"] + box["width"]  * random.uniform(0.25, 0.75)
    target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

    await human_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.08, 0.35))   # Brief pause before clicking

    # Verify the point actually belongs to our element; LinkedIn overlays,
    # hover cards and toasts routinely sit on top of it and swallow the click.
    hit = True
    try:
        hit = bool(await element.evaluate(
            """(el, pt) => {
                const top = document.elementFromPoint(pt.x, pt.y);
                return !!top && (top === el || el.contains(top) || top.contains(el));
            }""",
            {"x": target_x, "y": target_y},
        ))
    except Exception:
        hit = True

    if hit:
        await page.mouse.click(target_x, target_y)
    else:
        logger.debug("Click point is covered by another element; using a direct element click")
        try:
            await element.click(timeout=3000)
        except Exception:
            # Last resort: dispatch the event straight to the element.
            await element.dispatch_event("click")

    await asyncio.sleep(random.uniform(0.2, 0.6))     # Brief pause after clicking
 
 
async def human_type(page: Page, target: str | Locator | ElementHandle, text: str) -> None:
    """
    Types text character by character with human-like variable speed.
    Includes occasional micro-pauses to simulate thinking.
    Accepts selector string, Locator, or ElementHandle.
    """
    element = None
    if isinstance(target, str):
        try:
            element = await page.wait_for_selector(target, timeout=5000)
        except Exception:
            element = None
    elif isinstance(target, Locator):
        try:
            element = await target.element_handle()
        except Exception:
            element = None
    elif isinstance(target, ElementHandle):
        element = target

    await human_click(page, target)
    await asyncio.sleep(random.uniform(0.3, 0.9))

    # If the click was swallowed the field never gains focus and every
    # keystroke is dropped silently.  Force focus before typing.
    if element is not None:
        try:
            focused = bool(await element.evaluate(
                "el => el === document.activeElement || el.contains(document.activeElement)"
            ))
        except Exception:
            focused = True
        if not focused:
            logger.debug("Field not focused after click; forcing focus before typing")
            try:
                await element.focus()
                await asyncio.sleep(0.2)
            except Exception:
                pass

    for char in text:
        await page.keyboard.type(char)
 
        if char == " ":
            delay = random.uniform(0.07, 0.18)
        elif char in ".,!?;:":
            delay = random.uniform(0.12, 0.35)    # Pause at punctuation
        else:
            delay = random.uniform(0.04, 0.17)
 
        # 3% chance of a longer distraction pause (phone rang, looked away)
        if random.random() < 0.03:
            delay += random.uniform(0.5, 2.0)
 
        await asyncio.sleep(delay)

    # Verify the text landed; fall back to a direct fill when it did not.
    if element is not None and text:
        try:
            current = await element.input_value()
        except Exception:
            try:
                current = await element.inner_text()
            except Exception:
                current = text
        if not (current or "").strip():
            logger.warning("⚠️ Typed text did not land in the field; retrying with fill()")
            try:
                await element.fill(text)
                await asyncio.sleep(0.3)
            except Exception as exc:
                logger.debug(f"fill() fallback failed: {exc}")
 
 
async def human_scroll(page: Page, direction: str = "down", distance: int = None) -> None:
    """
    Scrolls in natural chunks with pauses between each chunk,
    as if reading content while scrolling.
    """
    if distance is None:
        distance = random.randint(250, 700)
 
    steps = random.randint(3, 7)
    for _ in range(steps):
        chunk = (distance // steps) + random.randint(-40, 40)
        scroll_y = -chunk if direction == "up" else chunk
        await page.mouse.wheel(0, scroll_y)
        await asyncio.sleep(random.uniform(0.08, 0.4))   # Pause between scroll chunks
 
    # After scrolling, pause to "read" content
    await asyncio.sleep(random.uniform(1.2, 3.5))
 
 
async def random_idle_pause(min_sec: float = 2.0, max_sec: float = 8.0) -> None:
    """Generic pause between actions. Use between every major step."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


# Per-candidate visibility probe for the resilient selector pools.
# LinkedIn A/B-serves several login layouts, each with different markup. Once
# the login form is already painted, a missing selector should fail instantly
# (``is_visible()`` does not wait). A short wait is only used when the caller
# expects the page to still be hydrating. The previous 1.5s-per-candidate
# wait made a 16-selector email pool take ~24s even when the form was ready.
_SELECTOR_PROBE_TIMEOUT_MS = 250


async def _probe_visible(
    page: Page,
    selector: str,
    timeout_ms: int | None = None,
) -> Locator | None:
    """
    Resolve a selector string to a VISIBLE Locator, quickly.

    Returns None (never raises) when the candidate doesn't match a visible
    element. If the form is already loaded, ``is_visible()`` is instant and
    we do not sit out the probe timeout for every miss.
    """
    wait_ms = _SELECTOR_PROBE_TIMEOUT_MS if timeout_ms is None else timeout_ms
    try:
        probe = page.locator(selector).first
        try:
            if await probe.is_visible():
                return probe
        except Exception:
            pass
        if wait_ms <= 0:
            return None
        await probe.wait_for(state="visible", timeout=wait_ms)
        return probe
    except Exception:
        return None


async def find_and_type_resilient(
    page: Page,
    selectors: list[str] | list[Locator] | list[ElementHandle],
    value: str,
    field_name: str,
    probe_timeout_ms: int | None = None,
) -> str | Locator:
    """
    Iterates through a list of potential selectors/locators for a field.
    Finds the active one, and types the value like a human.
    Accepts selector strings, Locators, or ElementHandles.
    Returns the successful selector or Locator.
    """
    for target in selectors:
        try:
            if isinstance(target, str):
                # Only interact with candidates that resolve to a VISIBLE
                # element — skips hidden duplicate forms LinkedIn renders
                # for A/B tests, instead of timing out on them one by one.
                probe = await _probe_visible(page, target, timeout_ms=probe_timeout_ms)
                if probe is None:
                    continue
                if should_log_debug():
                    logger.debug(f"Found active selector for {field_name}: '{target}'")
                await human_type(page, probe, value)
                return target
            elif isinstance(target, (Locator, ElementHandle)):
                # For Locators/ElementHandles, check if they're visible and enabled
                is_visible = await target.is_visible()
                is_enabled = await target.is_enabled()

                if is_visible and is_enabled:
                    if should_log_debug():
                        logger.debug(f"Found active locator for {field_name}")
                    await human_type(page, target, value)
                    return target
        except Exception:
            # Continue checking fallback selectors if one fails
            continue

    raise ValueError(f"CRITICAL: Failed to locate any valid input selector for {field_name} (Checked: {selectors})")


async def find_and_click_resilient(page: Page, selectors: list[str] | list[Locator] | list[ElementHandle], button_name: str) -> str | Locator:
    """
    Iterates through fallback selectors/locators to find and click a button.
    Accepts selector strings, Locators, or ElementHandles.
    Returns the successful selector or Locator.
    """
    for target in selectors:
        try:
            if isinstance(target, str):
                # Only click candidates that resolve to a VISIBLE element
                # (see _probe_visible for why a fast probe replaced the old
                # 3s "attached" + 5s "visible" double-wait).
                probe = await _probe_visible(page, target, timeout_ms=probe_timeout_ms)
                if probe is None:
                    continue
                if should_log_debug():
                    logger.debug(f"Found active selector for {button_name}: '{target}'")
                await human_click(page, probe)
                return target
            elif isinstance(target, (Locator, ElementHandle)):
                # For Locators/ElementHandles, check if they're visible and enabled
                is_visible = await target.is_visible()
                is_enabled = await target.is_enabled()

                if is_visible and is_enabled:
                    if should_log_debug():
                        logger.debug(f"Found active locator for {button_name}")
                    await human_click(page, target)
                    return target
        except Exception:
            continue

    raise ValueError(f"CRITICAL: Failed to locate any valid click selector for {button_name} (Checked: {selectors})")

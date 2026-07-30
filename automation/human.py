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
from core.logging_config import get_logger, should_log_debug

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
            # On failure, save a screenshot for debugging
            screenshot_path = f"error_screenshot_{random.randint(1000, 9999)}.png"
            await page.screenshot(path=screenshot_path, full_page=True)
            # Re-raise the exception with more context
            raise type(e)(
                f"Failed to find selector '{target}'. "
                f"Screenshot saved to '{screenshot_path}'. "
                f"Current URL: {page.url}. Original error: {e}"
            ) from e
    elif isinstance(target, Locator):
        element = await target.element_handle()
    elif isinstance(target, ElementHandle):
        element = target
    else:
        raise TypeError(f"human_click() expects str, Locator, or ElementHandle, got {type(target)}")

    box = await element.bounding_box()
    if not box:
        # Fallback for elements without a bounding box (e.g., some SVGs)
        await element.click()
        return
 
    # Click slightly off-centre — humans don't click exact pixel-perfect centres
    target_x = box["x"] + box["width"]  * random.uniform(0.25, 0.75)
    target_y = box["y"] + box["height"] * random.uniform(0.25, 0.75)

    await human_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.08, 0.35))   # Brief pause before clicking
    await page.mouse.click(target_x, target_y)
    await asyncio.sleep(random.uniform(0.2, 0.6))     # Brief pause after clicking
 
 
async def human_type(page: Page, target: str | Locator | ElementHandle, text: str) -> None:
    """
    Types text character by character with human-like variable speed.
    Includes occasional micro-pauses to simulate thinking.
    Accepts selector string, Locator, or ElementHandle.
    """
    await human_click(page, target)
    await asyncio.sleep(random.uniform(0.3, 0.9))
 
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


async def find_and_type_resilient(page: Page, selectors: list[str] | list[Locator] | list[ElementHandle], value: str, field_name: str) -> str | Locator:
    """
    Iterates through a list of potential selectors/locators for a field.
    Finds the active one, and types the value like a human.
    Accepts selector strings, Locators, or ElementHandles.
    Returns the successful selector or Locator.
    """
    for target in selectors:
        try:
            if isinstance(target, str):
                # Check if selector is attached to DOM (not necessarily visible yet)
                await page.wait_for_selector(target, timeout=3000, state="attached")
                if should_log_debug():
                    logger.debug(f"Found active selector for {field_name}: '{target}'")
                await human_type(page, target, value)
                return target
            elif isinstance(target, (Locator, ElementHandle)):
                # For Locators/ElementHandles, check if they're visible and enabled
                if isinstance(target, Locator):
                    is_visible = await target.is_visible()
                    is_enabled = await target.is_enabled()
                else:
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
                element = await page.wait_for_selector(target, timeout=3000, state="attached")
                if element:
                    if should_log_debug():
                        logger.debug(f"Found active selector for {button_name}: '{target}'")
                    await human_click(page, target)
                    return target
            elif isinstance(target, (Locator, ElementHandle)):
                # For Locators/ElementHandles, check if they're visible and enabled
                if isinstance(target, Locator):
                    is_visible = await target.is_visible()
                    is_enabled = await target.is_enabled()
                else:
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

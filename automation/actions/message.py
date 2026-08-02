"""
Action: Send a LinkedIn direct message.
FILE: automation/actions/message.py
 
Used for Day 3 (intro message), Day 4 (follow-up if pending),
and Day 5 (thanks message if accepted).
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
        await page.goto(profile_url, wait_until="domcontentloaded")
        await random_idle_pause(3, 5)
 
        # Natural scroll before clicking
        await human_scroll(page)
        await random_idle_pause(1.5, 3.5)
 
        message_btn = await page.query_selector("button[aria-label*='Message']")
        if not message_btn:
            result["error"] = "Message button not found — not a 1st-degree connection yet"
            return result
 
        await human_click(page, "button[aria-label*='Message']")
        await random_idle_pause(1.0, 2.5)
 
        # Type the message in the compose box
        compose_box = await page.query_selector(
            "div.msg-form__contenteditable[contenteditable='true']"
        )
        if not compose_box:
            result["error"] = "Message compose box not found"
            return result
 
        # Click the compose box and type
        box = await compose_box.bounding_box()
        if box:
            await human_mouse_move(page, box["x"] + 20, box["y"] + 10)
        await page.click("div.msg-form__contenteditable[contenteditable='true']")
        await random_idle_pause(0.3, 0.8)
 
        # Type character by character with human speed
        for char in message:
            await page.keyboard.type(char)
            delay = random.uniform(0.04, 0.18)
            if char in " .,!?":
                delay = random.uniform(0.08, 0.25)
            await asyncio.sleep(delay)
 
        await random_idle_pause(1.0, 2.5)
 
        # Send with Enter key (more natural than clicking Send button)
        if random.random() < 0.7:
            await page.keyboard.press("Enter")
        else:
            await human_click(page, "button.msg-form__send-button")
 
        await random_idle_pause(2, 4)
        result["sent"] = True
 
    except Exception as e:
        result["error"] = str(e)
 
    return result

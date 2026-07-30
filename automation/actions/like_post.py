"""
Action: Visit a LinkedIn profile and optionally like their most recent post.
FILE: automation/actions/like_post.py
 
This is Day 2 of the drip sequence. The goal is to like the target's most recent post,
which increases connection acceptance rate and engagement.
"""
import asyncio
import random
from patchright.async_api import Page
from automation.human import human_click, human_scroll, human_mouse_move, random_idle_pause
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)
async def visit_profile_and_like_post(page: Page, profile_url: str) -> dict:
    """
    Visits a LinkedIn profile and likes the first visible post if available.
    
    Returns a dict with:
      - visited: bool
      - liked_post: bool
      - profile_name: str or None
      - error: str or None
    """
    result = {"visited": False, "liked_post": False, "profile_name": None, "error": None}
 
    try:
        # TODO: Decrease timeout for production (currently 120s for local network development)
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(2, 4)
 
        # Verify we landed on a profile page (not a 404 or login redirect)
        if "/in/" not in page.url:
            result["error"] = f"Unexpected URL after navigation: {page.url}"
            return result
 
        result["visited"] = True
 
        # Extract name for logging
        try:
            name_el = await page.query_selector("h1.text-heading-xlarge")
            if name_el:
                result["profile_name"] = await name_el.inner_text()
        except Exception:
            pass
 
        # Scroll through the profile naturally (as if reading)
        for _ in range(random.randint(2, 4)):
            await human_scroll(page)
            await random_idle_pause(1, 2.5)
 
        # Hover on some profile sections (experience, skills) — adds realism
        sections = await page.query_selector_all("section.artdeco-card")
        if sections:
            hover_target = random.choice(sections[:3])
            box = await hover_target.bounding_box()
            if box:
                await human_mouse_move(page, box["x"] + 50, box["y"] + 20)
                await random_idle_pause(0.5, 1.5)
 
        # ── Like the most recent post ─────────────────────────────────────────
        # Navigate to their recent activity feed
        activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
        # TODO: Decrease timeout for production (currently 120s for local network development)
        await page.goto(activity_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(1, 3)
 
        # Scroll a little to load posts
        await human_scroll(page)
        await random_idle_pause(1, 3)
 
        # Find the first Like button (not already liked)
        # Try multiple selectors for better compatibility
        like_selectors = [
            "button[aria-label*='Like'][aria-pressed='false']",
            "button[aria-label*='like'][aria-pressed='false']",
            "button[aria-label*='React'][aria-pressed='false']",
            "button[aria-label*='react'][aria-pressed='false']",
        ]
        
        like_btn = None
        for selector in like_selectors:
            try:
                like_btn = await page.query_selector(selector)
                if like_btn:
                    logger.debug(f"Found like button with selector: {selector}")
                    break
            except:
                continue
        if like_btn:
            box = await like_btn.bounding_box()
            if box:
                # Get aria-label before click (development only)
                if should_log_debug():
                    aria_before = await like_btn.get_attribute("aria-label")
                    logger.debug(f"Like button aria-label before click: {aria_before}")
                
                target_x = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
                target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await human_mouse_move(page, target_x, target_y)
                await random_idle_pause(0.5, 1.5)
                await page.mouse.click(target_x, target_y)
                await random_idle_pause(2, 3)  # Increased wait time for LinkedIn to process
                
                # Verify like was successful by checking aria-label changed (development only)
                if should_log_debug():
                    aria_after = await like_btn.get_attribute("aria-label")
                    logger.debug(f"Like button aria-label after click: {aria_after}")
                    
                    # Check if aria-pressed changed to true
                    aria_pressed = await like_btn.get_attribute("aria-pressed")
                    logger.debug(f"Like button aria-pressed after click: {aria_pressed}")
                
                # Take screenshot for debugging (development only)
                if should_take_screenshots():
                    await page.screenshot(path="like_action_debug.png", full_page=True)
                
                # Verify like was successful
                aria_pressed = await like_btn.get_attribute("aria-pressed")
                aria_after = await like_btn.get_attribute("aria-label")
                if aria_pressed == "true" or (aria_after and "unlike" in aria_after.lower()):
                    result["liked_post"] = True
                    logger.info("✅ Like action verified - button state changed")
                else:
                    logger.warning("⚠️ Like action may have failed - button state unchanged")
                    result["liked_post"] = False
 
    except Exception as e:
        result["error"] = str(e)
 
    return result

"""
Action: Visit a LinkedIn profile and/or like their most recent post.
FILE: automation/actions/visit_profile.py

Provides three public coroutines:

  visit_profile(page, profile_url)
      Navigate to the profile, scroll naturally, hover sections.
      Does NOT touch the activity feed or any like button.

  like_recent_post(page, profile_url)
      Navigate directly to the lead's recent-activity feed and like
      the first un-liked post.  Does NOT re-visit the profile page.

  visit_profile_and_like_post(page, profile_url)
      Thin combinator used by the VISIT_AND_LIKE step type and
      legacy Celery tasks.  Calls both functions above in sequence.
"""
import random
from playwright.async_api import Page
from automation.human import human_scroll, human_mouse_move, random_idle_pause
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)


# ── Pure visit ────────────────────────────────────────────────────────────────

async def visit_profile(page: Page, profile_url: str) -> dict:
    """
    Navigate to a LinkedIn profile and browse it naturally.

    Appears on the target's "Who viewed your profile" list without
    touching the activity feed or any like button.

    Returns a dict with:
      - visited:      bool
      - profile_name: str or None
      - error:        str or None
    """
    result = {"visited": False, "profile_name": None, "error": None}

    try:
        # TODO: Decrease timeout for production (currently 120s for local network development)
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=120000)
        await random_idle_pause(3, 6)

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
            await random_idle_pause(1.5, 4.0)

        # Hover on some profile sections (experience, skills) — adds realism
        sections = await page.query_selector_all("section.artdeco-card")
        if sections:
            hover_target = random.choice(sections[:3])
            box = await hover_target.bounding_box()
            if box:
                await human_mouse_move(page, box["x"] + 50, box["y"] + 20)
                await random_idle_pause(1.0, 3.0)

    except Exception as e:
        result["error"] = str(e)

    return result


# ── Pure like ─────────────────────────────────────────────────────────────────

async def like_recent_post(page: Page, profile_url: str) -> dict:
    """
    Navigate to a lead's recent-activity feed and like their first post.

    Does NOT visit the profile page — call visit_profile() first if a
    profile view is also required (or use visit_profile_and_like_post).

    Returns a dict with:
      - liked_post: bool
      - error:      str or None
    """
    result = {"liked_post": False, "error": None}

    try:
        activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
        # TODO: Decrease timeout for production (currently 120s for local network development)
        await page.goto(activity_url, wait_until="domcontentloaded", timeout=120000)
        await random_idle_pause(2, 5)

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
            except Exception:
                continue

        if like_btn:
            box = await like_btn.bounding_box()
            if box:
                # Log aria-label before click (development only)
                if should_log_debug():
                    aria_before = await like_btn.get_attribute("aria-label")
                    logger.debug(f"Like button aria-label before click: {aria_before}")

                target_x = box["x"] + box["width"]  * random.uniform(0.3, 0.7)
                target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                await human_mouse_move(page, target_x, target_y)
                await random_idle_pause(0.5, 1.5)
                await page.mouse.click(target_x, target_y)
                await random_idle_pause(2, 3)  # Wait for LinkedIn to process the like

                # Log aria-label after click (development only)
                if should_log_debug():
                    aria_after = await like_btn.get_attribute("aria-label")
                    logger.debug(f"Like button aria-label after click: {aria_after}")
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


# ── Combinator (VISIT_AND_LIKE step type + legacy tasks) ──────────────────────

async def visit_profile_and_like_post(page: Page, profile_url: str) -> dict:
    """
    Visit the profile AND like the most recent post in one call.

    Used by:
      - CampaignStepType.VISIT_AND_LIKE
      - Legacy step1_visit_and_like Celery task

    Returns a merged dict with keys from both visit_profile() and
    like_recent_post():
      - visited:      bool
      - liked_post:   bool
      - profile_name: str or None
      - error:        str or None  (first error encountered, if any)
    """
    visit_result = await visit_profile(page, profile_url)

    # Abort the like step if the profile visit itself failed
    if not visit_result["visited"]:
        return {
            "visited": False,
            "liked_post": False,
            "profile_name": visit_result.get("profile_name"),
            "error": visit_result.get("error"),
        }

    like_result = await like_recent_post(page, profile_url)

    return {
        "visited": visit_result["visited"],
        "liked_post": like_result["liked_post"],
        "profile_name": visit_result.get("profile_name"),
        # Surface whichever error occurred first
        "error": visit_result.get("error") or like_result.get("error"),
    }

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
from patchright.async_api import Page
from automation.human import human_scroll, human_mouse_move, random_idle_pause
from automation.actions.utils import is_blank_page
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
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(2, 4)

        # Handle blank page — reload once
        if await is_blank_page(page):
            logger.warning("⚠️ Blank page detected on profile visit, reloading...")
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            await random_idle_pause(2, 4)

        # Verify we landed on a profile page (not a 404 or login redirect)
        if "/in/" not in page.url:
            if "/authwall" in page.url or "/login" in page.url or "/checkpoint" in page.url:
                result["error"] = (
                    f"Session is not authenticated — LinkedIn redirected to {page.url}"
                )
            else:
                result["error"] = f"Unexpected URL after navigation: {page.url}"
            return result

        # A removed/restricted profile still lives under /in/ but renders an
        # error page, so the URL check alone is not enough.
        unavailable = await _profile_unavailable(page)
        if unavailable:
            result["error"] = unavailable
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

    except Exception as e:
        result["error"] = str(e)

    return result


async def _profile_unavailable(page: Page) -> str | None:
    """Detect removed / restricted / not-found profiles rendered under /in/."""
    try:
        await page.wait_for_selector("h1", state="visible", timeout=8000)
    except Exception:
        pass
    try:
        body = " ".join((await page.inner_text("body")).split()).lower()
    except Exception:
        return None
    for hint, reason in [
        ("this page doesn't exist", "Profile does not exist (404)."),
        ("page not found", "Profile does not exist (404)."),
        ("profile is not available", "Profile is not available."),
        ("this profile is not available", "Profile is not available."),
        ("account has been restricted", "Profile account is restricted."),
        ("member's profile is not available", "Profile is not available."),
    ]:
        if hint in body:
            return reason
    return None


# ── Pure like ─────────────────────────────────────────────────────────────────

async def like_recent_post(page: Page, profile_url: str) -> dict:
    """
    Navigate to a lead's recent-activity feed and like their first post.

    Does NOT visit the profile page — call visit_profile() first if a
    profile view is also required (or use visit_profile_and_like_post).

    Returns a dict with:
      - liked_post: bool
      - error:      str or None

    Every non-like outcome is reported explicitly instead of silently
    returning ``liked_post=False, error=None``:
      * the lead has no posts at all,
      * every visible post is already liked,
      * the like click was swallowed (retried via a direct element click),
      * LinkedIn showed a rejection/limit banner.
    """
    # ``skipped`` marks benign non-likes (no posts / everything already liked)
    # so the caller can avoid failing — and endlessly retrying — a lead that
    # simply has nothing likeable.
    result = {"liked_post": False, "skipped": False, "error": None}

    try:
        activity_url = profile_url.rstrip("/") + "/recent-activity/all/"
        await page.goto(activity_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(1, 3)

        # Handle blank page — reload once
        if await is_blank_page(page):
            logger.warning("⚠️ Blank page detected on activity feed, reloading...")
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            await random_idle_pause(1, 3)

        if "/recent-activity" not in page.url:
            result["error"] = f"Unexpected URL after navigating to activity feed: {page.url}"
            return result

        # Scroll to load posts (feeds lazy-render; one scroll is often not enough).
        for _ in range(random.randint(2, 3)):
            await human_scroll(page)
            await random_idle_pause(1, 2.5)

        like_btn = await _find_like_button(page)

        if not like_btn:
            if await _has_no_posts(page):
                result["skipped"] = True
                result["error"] = "Lead has no recent posts to like."
            elif await _already_liked(page):
                result["skipped"] = True
                result["error"] = "All visible posts are already liked."
            else:
                if should_take_screenshots():
                    try:
                        await page.screenshot(path="like_button_missing_debug.png", full_page=True)
                    except Exception:
                        pass
                result["error"] = "No like button found on the lead's recent activity feed."
            return result

        liked, like_error = await _click_like(page, like_btn)
        result["liked_post"] = liked
        result["error"] = like_error

    except Exception as e:
        result["error"] = str(e)

    return result


async def _find_like_button(page: Page):
    """Return the first visible, un-liked Like/React button, else None."""
    like_selectors = [
        "button[aria-label*='Like'][aria-pressed='false']",
        "button[aria-label*='like'][aria-pressed='false']",
        "button[aria-label*='React'][aria-pressed='false']",
        "button[aria-label*='react'][aria-pressed='false']",
        "button.react-button__trigger[aria-pressed='false']",
    ]
    for selector in like_selectors:
        try:
            candidates = await page.query_selector_all(selector)
        except Exception:
            continue
        for candidate in candidates:
            try:
                # Skip off-screen/hidden buttons — clicking their coordinates
                # lands on whatever is actually painted there instead.
                if not await candidate.is_visible():
                    continue
                await candidate.scroll_into_view_if_needed()
                if not await candidate.bounding_box():
                    continue
            except Exception:
                continue
            if should_log_debug():
                logger.debug(f"Found like button with selector: {selector}")
            return candidate
    return None


async def _has_no_posts(page: Page) -> bool:
    """True when the activity feed genuinely contains no posts."""
    try:
        posts = await page.query_selector_all(
            "div.feed-shared-update-v2, div[data-urn], li.profile-creator-shared-feed-update__container"
        )
        if posts:
            return False
    except Exception:
        return False
    try:
        body = " ".join((await page.inner_text("main")).split()).lower()
    except Exception:
        return True
    return ("hasn't posted" in body or "no posts" in body
            or "nothing to see" in body or not body)


async def _already_liked(page: Page) -> bool:
    """True when posts exist but every visible reaction button is pressed."""
    try:
        pressed = await page.query_selector_all("button[aria-pressed='true']")
    except Exception:
        return False
    return bool(pressed)


async def _like_state(button) -> bool:
    """Read a like button's pressed state, tolerating a detached handle."""
    try:
        aria_pressed = await button.get_attribute("aria-pressed")
        aria_label = (await button.get_attribute("aria-label")) or ""
    except Exception:
        # The button was re-rendered after the click, which LinkedIn only
        # does once the reaction registered.
        return True
    return aria_pressed == "true" or "unlike" in aria_label.lower()


async def _click_like(page: Page, like_btn) -> tuple[bool, str | None]:
    """Click a like button and verify the reaction registered."""
    box = await like_btn.bounding_box()
    if not box:
        return False, "Like button has no bounding box (hidden or detached)."

    if should_log_debug():
        aria_before = await like_btn.get_attribute("aria-label")
        logger.debug(f"Like button aria-label before click: {aria_before}")

    target_x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
    target_y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
    await human_mouse_move(page, target_x, target_y)
    await random_idle_pause(0.5, 1.5)
    await page.mouse.click(target_x, target_y)
    await random_idle_pause(2, 3)  # Wait for LinkedIn to process the like

    if should_take_screenshots():
        try:
            await page.screenshot(path="like_action_debug.png", full_page=True)
        except Exception:
            pass

    if await _like_state(like_btn):
        logger.info("✅ Like action verified - button state changed")
        return True, None

    # The coordinate click can be intercepted by the reactions fly-out or a
    # sticky header; retry once with a direct element click before failing.
    logger.warning("⚠️ Like not registered after the mouse click; retrying with a direct click")
    try:
        await like_btn.click(timeout=3000)
        await random_idle_pause(1.5, 2.5)
    except Exception as exc:
        logger.debug(f"Direct like click failed: {exc}")

    if await _like_state(like_btn):
        logger.info("✅ Like action verified after the direct-click retry")
        return True, None

    banner = await _reaction_error_text(page)
    if banner:
        logger.error("❌ LinkedIn rejected the reaction: %s", banner)
        return False, f"LinkedIn rejected the reaction: {banner}"

    logger.warning("⚠️ Like action failed - button state unchanged")
    return False, "Like was clicked but LinkedIn never registered the reaction."


async def _reaction_error_text(page: Page) -> str | None:
    """Surface LinkedIn's own error toast, if the reaction was refused."""
    for selector in [
        "div[data-test-artdeco-toast-item-type='error']",
        ".artdeco-toast-item--error",
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
                text = " ".join((await node.inner_text()).split())
            except Exception:
                continue
            if text:
                return text[:240]
    return None


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
            "skipped": False,
            "profile_name": visit_result.get("profile_name"),
            "error": visit_result.get("error"),
        }

    like_result = await like_recent_post(page, profile_url)

    return {
        "visited": visit_result["visited"],
        "liked_post": like_result["liked_post"],
        "skipped": like_result.get("skipped", False),
        "profile_name": visit_result.get("profile_name"),
        # Surface whichever error occurred first
        "error": visit_result.get("error") or like_result.get("error"),
    }

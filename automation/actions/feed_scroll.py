"""
Action: Scroll LinkedIn feed and collect posts.
FILE: automation/actions/feed_scroll.py

Navigates to the LinkedIn feed, scrolls naturally, and extracts post content
for scoring. Uses existing human-like behavior functions.
"""
import random
from patchright.async_api import Page
from automation.human import human_scroll, random_idle_pause
from automation.actions.utils import is_blank_page
from core.logging_config import get_logger

logger = get_logger(__name__)


async def scroll_feed_and_collect(
    page: Page,
    target_posts: int = 20,
    max_scrolls: int = 15,
) -> list[dict]:
    """
    Navigate to LinkedIn feed, scroll naturally, and extract posts.

    Args:
        page: Playwright page object
        target_posts: Number of posts to try to collect
        max_scrolls: Maximum number of scroll iterations

    Returns:
        List of dicts with keys: post_urn, post_url, author_name, post_text
    """
    collected_posts = []
    seen_urns = set()

    try:
        # Navigate to the feed
        logger.info("Navigating to LinkedIn feed...")
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(2, 4)

        # Handle blank page — reload once
        if await is_blank_page(page):
            logger.warning("️ Blank page detected on feed, reloading...")
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            await random_idle_pause(2, 4)

        # Verify we're on the feed
        if "/feed" not in page.url:
            logger.warning(f"Unexpected URL after navigating to feed: {page.url}")
            return []

        logger.info("Starting feed scroll to collect posts...")

        for scroll_iteration in range(max_scrolls):
            if len(collected_posts) >= target_posts:
                logger.info(f"Collected {len(collected_posts)} posts, stopping scroll")
                break

            # Extract posts from current view
            new_posts = await _extract_visible_posts(page, seen_urns)
            for post in new_posts:
                if post["post_urn"] not in seen_urns:
                    seen_urns.add(post["post_urn"])
                    collected_posts.append(post)
                    logger.debug(f"Collected post {len(collected_posts)}: {post.get('author_name', 'Unknown')}")

            # Scroll naturally
            await human_scroll(page)
            await random_idle_pause(1.5, 3.0)

            logger.debug(f"Scroll {scroll_iteration + 1}: collected {len(collected_posts)} posts so far")

        logger.info(f"Feed scroll complete. Collected {len(collected_posts)} posts total")
        return collected_posts

    except Exception as e:
        logger.error(f"Error during feed scroll: {e}")
        return collected_posts


async def _extract_visible_posts(page: Page, seen_urns: set) -> list[dict]:
    """
    Extract post data from currently visible feed items.

    Returns list of dicts with: post_urn, post_url, author_name, post_text
    """
    posts = []

    # LinkedIn feed post selectors (multiple fallbacks)
    post_selectors = [
        "div.feed-shared-update-v2",
        "div[data-urn^='urn:li:activity:']",
        "div.occludable-update",
        "li.feed-shared-update-v2",
    ]

    post_elements = []
    for selector in post_selectors:
        try:
            elements = await page.query_selector_all(selector)
            if elements:
                post_elements = elements
                logger.debug(f"Found {len(elements)} posts with selector: {selector}")
                break
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")
            continue

    for element in post_elements:
        try:
            # Extract URN (unique identifier)
            post_urn = await _get_post_urn(element)
            if not post_urn or post_urn in seen_urns:
                continue

            # Extract post text
            post_text = await _get_post_text(element)
            if not post_text or len(post_text.strip()) < 20:
                continue

            # Extract author name
            author_name = await _get_author_name(element)

            # Build post URL
            post_url = f"https://www.linkedin.com/feed/update/{post_urn}/" if post_urn else None

            posts.append({
                "post_urn": post_urn,
                "post_url": post_url,
                "author_name": author_name,
                "post_text": post_text,
            })

        except Exception as e:
            logger.debug(f"Error extracting post data: {e}")
            continue

    return posts


async def _get_post_urn(element) -> str | None:
    """Extract the URN from a post element."""
    try:
        # Try data-urn attribute first
        urn = await element.get_attribute("data-urn")
        if urn:
            return urn

        # Try to find URN in the element's attributes or children
        for selector in ["[data-urn]", "[data-id]"]:
            child = await element.query_selector(selector)
            if child:
                urn = await child.get_attribute("data-urn") or await child.get_attribute("data-id")
                if urn:
                    return urn

        # Fallback: generate a pseudo-URN from element position
        return f"post_{hash(await element.inner_html())}"

    except Exception:
        return None


async def _get_post_text(element) -> str:
    """Extract the main text content from a post element."""
    try:
        # Try to find the main text container
        text_selectors = [
            "span.break-words",
            "div.feed-shared-text",
            "div[class*='update-components-text']",
            "p",
        ]

        for selector in text_selectors:
            text_el = await element.query_selector(selector)
            if text_el:
                text = await text_el.inner_text()
                if text and len(text.strip()) > 20:
                    return text.strip()

        # Fallback: get all text from the element
        full_text = await element.inner_text()
        return full_text.strip()[:2000]  # Limit to 2000 chars

    except Exception:
        return ""


async def _get_author_name(element) -> str | None:
    """Extract the author's name from a post element."""
    try:
        # Try common author name selectors
        author_selectors = [
            "a.feed-shared-actor__description",
            "span.feed-shared-actor__name",
            "a.actor-description",
            "h3 span[aria-hidden='true']",
        ]

        for selector in author_selectors:
            author_el = await element.query_selector(selector)
            if author_el:
                name = await author_el.inner_text()
                if name:
                    return name.strip()

        return None

    except Exception:
        return None

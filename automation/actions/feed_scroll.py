"""
Action: Scroll LinkedIn feed and collect posts.
FILE: automation/actions/feed_scroll.py

Navigates to the LinkedIn feed, scrolls naturally, and extracts post content
for scoring. Uses existing human-like behavior functions.

Includes robust modern selectors + explicit waits + heavy debug logging + screenshots.
"""
import asyncio
import random
import time
from patchright.async_api import Page
from automation.human import human_scroll, random_idle_pause
from automation.actions.utils import recover_blank_page
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)

# Force screenshots during feed scroll scans even outside dev mode.
# This is extremely valuable for debugging "why no posts?" issues.
FORCE_FEED_SCREENSHOTS = True


def _should_screenshot() -> bool:
    """Always take screenshots for feed scroll diagnostics."""
    return FORCE_FEED_SCREENSHOTS or should_take_screenshots()


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
        feed_url = "https://www.linkedin.com/feed/"
        await page.goto(feed_url, wait_until="domcontentloaded", timeout=30000)
        await random_idle_pause(2, 4)

        # Blank-page recovery (wait for render → reload → session probe → retry)
        recovered, load_error, session_stale = await recover_blank_page(page, feed_url)
        if not recovered:
            logger.error("❌ Feed failed to load: %s (session_stale=%s)", load_error, session_stale)
            return []

        # Verify we're on the feed
        if "/feed" not in page.url:
            logger.warning(f"Unexpected URL after navigating to feed: {page.url}")
            return []

        logger.info("✅ Feed page loaded. Waiting for posts to render...")

        # === NEW: Explicit wait + debug screenshot (FORCED for diagnostics) ===
        if _should_screenshot():
            try:
                await page.screenshot(path="feed_initial_load.png", full_page=True)
                logger.info("📸 Saved feed_initial_load.png")
            except Exception:
                pass

        # Wait for at least one post container to appear (critical!)
        try:
            await page.wait_for_selector(
                "div[data-urn], article, [data-urn*='activity'], "
                "div.feed-shared-update-v2, div.update-components-text, "
                ".scaffold-finite-scroll__item, .occludable-update",
                timeout=12000,
                state="attached"
            )
            logger.info("✅ At least one post-like element detected on feed")
        except Exception as wait_err:
            logger.warning(f"⚠️ No post container appeared within timeout: {wait_err}")
            if _should_screenshot():
                try:
                    await page.screenshot(path="feed_no_posts_after_wait.png", full_page=True)
                    logger.info("📸 Saved feed_no_posts_after_wait.png")
                except Exception:
                    pass

        # Small extra settle time for lazy-loaded content
        await random_idle_pause(1.5, 3.0)

        # === Extra diagnostic: count key selectors ===
        try:
            for diag_sel in ["div[data-urn]", "article", ".scaffold-finite-scroll__item"]:
                try:
                    count = await page.locator(diag_sel).count()
                    if count > 0:
                        logger.info(f"📍 Diagnostic: {count} elements matched '{diag_sel}'")
                except Exception:
                    pass
        except Exception:
            pass

        logger.info("Starting feed scroll to collect posts...")

        # Track how many post-like elements exist in the DOM so we can tell
        # whether LinkedIn actually appended new content after each scroll.
        dom_post_count = await _count_dom_posts(page)

        for scroll_iteration in range(max_scrolls):
            if len(collected_posts) >= target_posts:
                logger.info(f"Collected {len(collected_posts)} posts, stopping scroll")
                break

            # Extract posts from current view
            new_posts = await _extract_visible_posts(page, seen_urns)
            added_this_pass = 0
            for post in new_posts:
                if post["post_urn"] not in seen_urns:
                    seen_urns.add(post["post_urn"])
                    collected_posts.append(post)
                    added_this_pass += 1
                    logger.info(f"✅ Collected post #{len(collected_posts)}: {post.get('author_name', 'Unknown')[:40]}... (len={len(post.get('post_text',''))})")

            if added_this_pass == 0:
                logger.info(f"Scroll pass {scroll_iteration + 1}: 0 new posts extracted (total so far: {len(collected_posts)})")

            # === NEW: debug screenshot per scroll pass (FORCED) ===
            if _should_screenshot() and scroll_iteration % 3 == 0:
                try:
                    await page.screenshot(path=f"feed_scroll_pass_{scroll_iteration+1}.png", full_page=True)
                    logger.info(f"📸 Saved feed_scroll_pass_{scroll_iteration+1}.png")
                except Exception:
                    pass

            # Scroll naturally AND make sure LinkedIn actually loads more posts.
            # Bare mouse.wheel() often doesn't move LinkedIn's own scroll
            # container in headless mode, so the feed never grows and we end up
            # re-reading the same one screenful of posts.  We therefore also
            # scroll the real container with JS and wait for the DOM count to
            # increase before counting the pass as useful.
            old_dom_count = dom_post_count
            dom_post_count = await _feed_scroll_and_wait_for_new_posts(page, old_dom_count)

            if dom_post_count > old_dom_count:
                logger.info(
                    f"Scroll {scroll_iteration + 1}: LinkedIn loaded more posts "
                    f"(DOM {old_dom_count} -> {dom_post_count})"
                )
            else:
                logger.warning(
                    f"Scroll {scroll_iteration + 1}: no new posts loaded by LinkedIn "
                    f"(DOM still {dom_post_count}) - infinite scroll may not be triggering"
                )

            await random_idle_pause(1.5, 3.0)

            logger.info(f"Scroll {scroll_iteration + 1}/{max_scrolls}: collected {len(collected_posts)} posts so far")

        if len(collected_posts) == 0:
            logger.error("❌ CRITICAL: Feed scroll finished with ZERO posts collected after all scrolls!")
            if _should_screenshot():
                try:
                    await page.screenshot(path="feed_final_zero_posts.png", full_page=True)
                    logger.info("📸 Saved feed_final_zero_posts.png (last state)")
                except Exception:
                    pass

        logger.info(f"Feed scroll complete. Collected {len(collected_posts)} posts total")
        return collected_posts

    except Exception as e:
        logger.error(f"Error during feed scroll: {e}")
        return collected_posts


async def _count_dom_posts(page: Page) -> int:
    """Count post-like elements currently in the DOM (regardless of viewport)."""
    try:
        return await page.locator(
            "div[data-urn], article, .scaffold-finite-scroll__item"
        ).count()
    except Exception:
        return 0


async def _feed_scroll_and_wait_for_new_posts(
    page: Page,
    current_dom_count: int,
    max_wait: float = 10.0,
) -> int:
    """
    Scroll down far enough to trigger LinkedIn's infinite-scroll lazy loader,
    then wait until new posts are actually appended to the DOM.

    Root cause this fixes: a bare ``page.mouse.wheel()`` frequently does NOT
    move LinkedIn's feed in headless Chromium because the feed lives inside its
    own scrollable container (e.g. ``.scaffold-layout__main`` /
    ``.scaffold-finite-scroll__content``), so the window scrolls but the feed
    never grows and the same one screenful of posts is collected over and over.

    We drive the scroll both with the human-style wheel and with explicit JS
    that scrolls every scrollable candidate container to its bottom, then poll
    the DOM until the post count increases (new content loaded) or we time out.

    Returns the new post-element count in the DOM.
    """
    # Human-style wheel nudge first (keeps behaviour realistic).
    try:
        await page.mouse.move(720, 480)
    except Exception:
        pass
    await human_scroll(page)

    # Guarantee the real feed scroll container actually moves.
    try:
        await page.evaluate(
            """() => {
                const scroll = (el) => {
                    if (!el) return;
                    if (el.scrollHeight > el.clientHeight + 5) {
                        el.scrollTop = el.scrollHeight;
                    }
                };
                // Window / document scrolling element
                const doc = document.scrollingElement || document.documentElement;
                scroll(doc);
                window.scrollTo(0, document.body.scrollHeight);
                // Candidate feed scroll containers
                for (const sel of [
                    '.scaffold-layout__main',
                    '.scaffold-finite-scroll__content',
                    '.scaffold-finite-scroll__item',
                    'main',
                    '[class*="finite-scroll"]',
                    '[class*="feed"]'
                ]) {
                    document.querySelectorAll(sel).forEach(scroll);
                }
            }"""
        )
    except Exception as e:
        logger.debug(f"JS feed scroll failed: {e}")

    # Wait for the post count in the DOM to grow (LinkedIn appends new posts).
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        count = await _count_dom_posts(page)
        if count > current_dom_count:
            return count
        await asyncio.sleep(0.8)

    return current_dom_count


async def _extract_visible_posts(page: Page, seen_urns: set) -> list[dict]:
    """
    Extract post data from currently visible feed items.

    Returns list of dicts with: post_urn, post_url, author_name, post_text
    """
    posts = []

    # === UPDATED: Modern + many fallback selectors for 2025-2026 LinkedIn feed ===
    post_selectors = [
        # Newer primary containers (most reliable in recent LinkedIn)
        "div[data-urn]",
        "article[data-urn], article[data-id]",
        "div[data-urn*='activity'], div[data-id*='activity']",
        "div.scaffold-finite-scroll__item",
        "li.scaffold-finite-scroll__content > li",
        "div[data-testid*='feed'], div[class*='feed-shared-update']",
        "div.occludable-update",
        # Legacy fallbacks
        "div.feed-shared-update-v2",
        "div[data-urn^='urn:li:activity:']",
        "li.feed-shared-update-v2",
        "div.update-components-update",
        ".feed-update",
        "div[role='article']",
        "div[class*='occludable-update'], div[class*='update-components'], div[class*='feed-shared-update']",
    ]

    post_elements = []
    for selector in post_selectors:
        try:
            elements = await page.query_selector_all(selector)
            if elements:
                # Filter to only elements that look like real feed posts
                filtered = []
                for el in elements:
                    try:
                        # Quick sanity check: has either data-urn or some text
                        has_urn = await el.get_attribute("data-urn")
                        txt = await el.inner_text()
                        if has_urn or (txt and len(txt.strip()) > 30):
                            filtered.append(el)
                    except Exception:
                        filtered.append(el)  # be permissive

                if filtered:
                    post_elements = filtered
                    logger.info(f"🔍 Found {len(filtered)} potential posts using selector: {selector}")
                    break
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")
            continue

    if not post_elements:
        logger.warning("⚠️ _extract_visible_posts: No post elements found with any selector")
        if _should_screenshot():
            try:
                await page.screenshot(path="feed_extract_no_elements.png", full_page=True)
                logger.info("📸 Saved feed_extract_no_elements.png")
            except Exception:
                pass
        return posts

    logger.info(f"🔍 Attempting to extract data from {len(post_elements)} post elements")

    for idx, element in enumerate(post_elements):
        try:
            # Extract URN (unique identifier)
            post_urn = await _get_post_urn(element)
            if not post_urn or post_urn in seen_urns:
                continue

            # Extract post text (more lenient)
            post_text = await _get_post_text(element)
            if not post_text or len(post_text.strip()) < 15:  # relaxed from 20
                if should_log_debug():
                    logger.debug(f"Post {idx}: text too short or empty ({len(post_text or '')} chars), skipping")
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

            if should_log_debug():
                logger.debug(f"Extracted post {len(posts)}: urn={post_urn[:30] if post_urn else 'N/A'}... text_len={len(post_text)}")

        except Exception as e:
            logger.debug(f"Error extracting post data (idx={idx}): {e}")
            continue

    return posts


async def _get_post_urn(element) -> str | None:
    """Extract the URN from a post element. More aggressive now."""
    try:
        # 1. Try data-urn on the element itself
        urn = await element.get_attribute("data-urn")
        if urn and "urn:li:activity" in urn or urn and urn.startswith("urn:li:"):
            return urn

        # 2. Try data-urn anywhere inside (most common now)
        for sel in ["[data-urn]", "[data-id]"]:
            try:
                child = await element.query_selector(sel)
                if child:
                    urn = await child.get_attribute("data-urn") or await child.get_attribute("data-id")
                    if urn and ("urn:li:activity" in str(urn) or str(urn).startswith("urn:li:")):
                        return urn
            except Exception:
                continue

        # 3. Try common modern attributes
        for attr in ["data-urn", "id"]:
            try:
                val = await element.get_attribute(attr)
                if val and "activity" in str(val).lower():
                    return val
            except Exception:
                pass

        # 4. Last resort pseudo-URN (still useful for dedup in same run)
        html_snippet = (await element.inner_html())[:300] if await element.inner_html() else ""
        return f"post_{abs(hash(html_snippet)) % 100000000}"

    except Exception as e:
        if should_log_debug():
            logger.debug(f"_get_post_urn failed: {e}")
        return None


async def _get_post_text(element) -> str:
    """Extract the main text content from a post element. Much more aggressive."""
    try:
        # Updated 2025/2026 text selectors (order matters)
        text_selectors = [
            "div.update-components-text",
            "span.break-words",
            "div.feed-shared-text",
            "div[class*='update-components-text']",
            "div[class*='feed-shared-text']",
            "div[role='presentation'] span",   # sometimes used
            "p",
            ".feed-shared-inline-show-more-text",
        ]

        for selector in text_selectors:
            try:
                text_el = await element.query_selector(selector)
                if text_el:
                    text = await text_el.inner_text()
                    if text and len(text.strip()) > 12:
                        cleaned = " ".join(text.strip().split())
                        return cleaned[:2500]
            except Exception:
                continue

        # Fallback 1: Try to get the biggest text block inside the post
        try:
            all_text = await element.inner_text()
            # Clean and take a meaningful chunk
            cleaned = " ".join(all_text.split())
            if len(cleaned) > 15:
                return cleaned[:2500]
        except Exception:
            pass

        # Fallback 2: whole element text (raw)
        full_text = await element.inner_text()
        return (full_text or "").strip()[:2500]

    except Exception as e:
        if should_log_debug():
            logger.debug(f"_get_post_text failed: {e}")
        return ""


async def _get_author_name(element) -> str | None:
    """Extract the author's name from a post element. More robust."""
    try:
        author_selectors = [
            # Modern
            "a[href*='/in/'] span[aria-hidden='true']",
            "span.feed-shared-actor__name",
            "a.actor-description span",
            "div.feed-shared-actor__meta a",
            "h3 span[aria-hidden='true']",
            # Legacy
            "a.feed-shared-actor__description",
            "span.feed-shared-actor__name",
            "a.actor-description",
            "[data-control-name='actor_container'] span",
        ]

        for selector in author_selectors:
            try:
                author_el = await element.query_selector(selector)
                if author_el:
                    name = await author_el.inner_text()
                    name = (name or "").strip()
                    if name and len(name) > 1 and not name.lower().startswith(("like", "comment", "repost")):
                        return name[:80]
            except Exception:
                continue

        # Last-ditch: look for any prominent link near top
        try:
            links = await element.query_selector_all("a[href*='/in/']")
            for link in links[:2]:
                txt = await link.inner_text()
                if txt and len(txt.strip()) > 2 and len(txt.strip()) < 60:
                    return txt.strip()[:80]
        except Exception:
            pass

        return None

    except Exception as e:
        if should_log_debug():
            logger.debug(f"_get_author_name failed: {e}")
        return None

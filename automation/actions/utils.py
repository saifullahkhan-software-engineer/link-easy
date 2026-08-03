"""
Shared utilities for browser action modules.
FILE: automation/actions/utils.py

Common helpers used across visit_profile, connect, message, etc.
"""
from patchright.async_api import Page


def _normalize(text: str | None) -> str:
    """Collapse whitespace and strip — safe against None input."""
    return " ".join((text or "").split())


async def is_blank_page(page: Page) -> bool:
    """Detect if the page is blank/white (no meaningful content rendered).

    LinkedIn sometimes serves a blank page when bot detection triggers,
    the session is stale, or a challenge/captcha needs to be solved.
    We check both the body text length and whether key LinkedIn
    structural elements exist.

    Returns ``True`` if the page appears blank or did not fully render.
    """
    try:
        # Quick check: is there any substantial text content?
        body_text = _normalize(await page.inner_text("body"))
        if len(body_text) < 100:
            # Very little text — likely a blank page or challenge page
            return True

        # Check for LinkedIn's main app container
        app_root = await page.query_selector("#app-mount, .scaffold-layout__main")
        if not app_root:
            # No main app structure — page didn't render properly
            return True

        return False
    except Exception:
        # If we can't even query the body, something is very wrong
        return True


async def navigate_to_profile(page: Page, profile_url: str) -> str | None:
    """Navigate to a LinkedIn profile and handle blank-page / session issues.

    Returns ``None`` on success, or an error string describing why
    navigation failed.  Handles:
      * Blank/white pages (LinkedIn bot detection or stale session)
      * Redirects to login/authwall/checkpoint pages
      * Unexpected URLs

    On a blank page the function reloads once and retries.
    """
    try:
        await page.goto(profile_url, wait_until="networkidle", timeout=30000)
    except Exception as exc:
        return f"Navigation failed: {exc}"

    # Detect blank page — reload once
    if await is_blank_page(page):
        try:
            await page.reload(wait_until="networkidle", timeout=30000)
        except Exception as exc:
            return f"Reload after blank page failed: {exc}"
        if await is_blank_page(page):
            return "Page is blank after reload; session may be stale or LinkedIn is blocking."

    if "/in/" not in page.url:
        if any(token in page.url for token in ("/authwall", "/login", "/checkpoint")):
            return f"Session is not authenticated — LinkedIn redirected to {page.url}"
        return f"Unexpected URL after navigation: {page.url}"

    return None

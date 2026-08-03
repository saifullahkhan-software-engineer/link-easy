"""
Shared utilities for browser action modules.
FILE: automation/actions/utils.py

Common helpers used across visit_profile, connect, message, etc.
"""
import asyncio

from patchright.async_api import Page

from core.logging_config import get_logger

logger = get_logger(__name__)

# A known-good authenticated page used to probe whether the session is alive.
SESSION_HEALTH_URL = "https://www.linkedin.com/feed/"

# How long to give LinkedIn's SPA to mount before declaring a page blank.
# Navigations wait only for ``domcontentloaded`` (networkidle never fires on
# LinkedIn), so React may still be mounting when the first check runs.
RENDER_WAIT_SECONDS = 12.0

# URL fragments that indicate the session is no longer authenticated.
_AUTH_URL_TOKENS = ("/authwall", "/login", "/uas/login", "/checkpoint", "/verify")


def _normalize(text: str | None) -> str:
    """Collapse whitespace and strip — safe against None input."""
    return " ".join((text or "").split())


async def is_blank_page(page: Page) -> bool:
    """Detect if the page is blank/white (no meaningful content rendered).

    LinkedIn sometimes serves a blank page when bot detection triggers,
    the session is stale, or a challenge/captcha needs to be solved.

    The check is intentionally based on *text content only*: a page with
    substantial text has rendered — even if it is an authwall, login, or
    checkpoint page rather than the profile (those are classified by URL,
    not treated as blank).  Previously this also required LinkedIn's app
    container (``#app-mount``), which mis-labelled authwall pages as blank
    and masked the real "session is not authenticated" cause.

    Returns ``True`` if the page appears blank or did not fully render.
    """
    try:
        # Quick check: is there any substantial text content?
        body_text = _normalize(await page.inner_text("body"))
        if len(body_text) < 100:
            # Very little text — likely a blank page or challenge page
            return True

        return False
    except Exception:
        # If we can't even query the body, something is very wrong
        return True


def auth_redirect_url(page: Page) -> str | None:
    """Return the current URL if LinkedIn redirected us off the target page.

    Landing on login/authwall/checkpoint means the session is not usable for
    authenticated actions (e.g. sending connection requests), even when the
    page itself rendered fine.
    """
    url = page.url or ""
    for token in _AUTH_URL_TOKENS:
        if token in url:
            return url
    return None


async def wait_for_page_render(page: Page, timeout_seconds: float = RENDER_WAIT_SECONDS) -> bool:
    """Poll until the page no longer looks blank.

    A single ``is_blank_page()`` check right after ``domcontentloaded`` is
    racy: LinkedIn mounts most of the DOM from JavaScript, so a page that
    *will* render fine can look blank for several seconds.  This waits up to
    ``timeout_seconds`` for the SPA to mount before giving up.

    Returns ``True`` once the page has rendered, ``False`` on timeout.
    """
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
        if not await is_blank_page(page):
            return True
        if asyncio.get_running_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.5)


async def _session_health(page: Page) -> str:
    """Probe the session by loading a known-good page (the feed).

    Returns:
      * ``"ok"``          — the feed rendered; the session is alive.
      * ``"stale"``       — LinkedIn redirected to login/checkpoint/authwall.
      * ``"unreachable"`` — even the feed stayed blank or failed to load
                            (bot detection, restriction, or network failure).
    """
    try:
        await page.goto(SESSION_HEALTH_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("⚠️ Session health probe failed to navigate: %s", exc)
        return "unreachable"

    url = page.url or ""
    if any(token in url for token in _AUTH_URL_TOKENS):
        return "stale"

    if await wait_for_page_render(page, timeout_seconds=10.0):
        return "ok"
    return "unreachable"


async def recover_blank_page(page: Page, target_url: str) -> tuple[bool, str | None, bool]:
    """Progressive recovery when a page looks blank after navigation.

    Steps:
      1. Wait for the SPA to finish mounting (fixes false positives from
         checking immediately after ``domcontentloaded``).
      2. Reload once and wait for rendering again.
      3. Probe the session on a known-good page (the feed):
           - redirected to login/checkpoint → session is stale;
           - feed renders → retry the original navigation once;
           - everything is blank → stop (likely bot detection/restriction).

    Returns ``(recovered, error, session_stale)``:
      * ``recovered``      — the target page is now rendered.
      * ``error``          — human-readable reason when not recovered.
      * ``session_stale``  — True when the session itself is dead/unusable
                             and the account session should be stopped.
    """
    def _auth_failure() -> tuple[bool, str | None, bool] | None:
        """If LinkedIn parked us on an auth wall / checkpoint, report it."""
        auth_url = auth_redirect_url(page)
        if auth_url:
            return (
                False,
                f"Session is not authenticated — LinkedIn redirected to {auth_url}. "
                f"Re-login is required.",
                True,
            )
        return None

    # 0. Auth walls and checkpoints render with content — classify them by
    #    URL instead of waiting them out as "slow renders".
    auth_failure = _auth_failure()
    if auth_failure:
        return auth_failure

    # 1. Give the SPA time to mount — most "blank pages" are just slow renders.
    if await wait_for_page_render(page):
        return _auth_failure() or (True, None, False)

    # 2. Genuine blank page — reload once and wait for rendering.
    logger.warning("⚠️ Blank page detected, reloading...")
    try:
        await page.reload(wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        logger.warning("⚠️ Reload raised: %s", exc)
    auth_failure = _auth_failure()
    if auth_failure:
        return auth_failure
    if await wait_for_page_render(page):
        return _auth_failure() or (True, None, False)

    # 3. Still blank — find out whether the session itself is dead.
    logger.warning("⚠️ Page still blank after reload; checking session health...")
    health = await _session_health(page)

    if health == "stale":
        return (
            False,
            "LinkedIn session expired or was blocked — redirected to "
            "login/checkpoint. Re-login is required.",
            True,
        )

    if health == "ok":
        # Session is alive; the original navigation most likely hiccuped.
        logger.info("🔄 Session is healthy; retrying navigation to %s", target_url)
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:
            logger.warning("⚠️ Retry navigation raised: %s", exc)
        auth_failure = _auth_failure()
        if auth_failure:
            return auth_failure
        if await wait_for_page_render(page):
            return _auth_failure() or (True, None, False)
        return (
            False,
            "Page failed to load even though the session is active "
            f"(last URL: {page.url}). LinkedIn may be throttling requests; "
            "the step can be retried later.",
            False,
        )

    # Everything is blank — the session is unusable (bot detection,
    # restriction, or network failure). Report it as stale so the caller
    # stops hammering LinkedIn with further actions.
    return (
        False,
        f"LinkedIn is no longer serving pages (the feed is blank too; last URL: {page.url}). "
        "The session is stale or the account is restricted.",
        True,
    )


async def navigate_to_profile(page: Page, profile_url: str) -> str | None:
    """Navigate to a LinkedIn profile and handle blank-page / session issues.

    Returns ``None`` on success, or an error string describing why
    navigation failed.  Handles:
      * Blank/white pages (LinkedIn bot detection or stale session)
      * Redirects to login/authwall/checkpoint pages
      * Unexpected URLs

    On a blank page the progressive ``recover_blank_page()`` recovery is
    used (wait for render → reload → session health probe → retry).
    """
    try:
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
    except Exception as exc:
        return f"Navigation failed: {exc}"

    recovered, error, _session_stale = await recover_blank_page(page, profile_url)
    if not recovered:
        return error or "Page is blank after recovery attempts."

    if "/in/" not in page.url:
        if any(token in page.url for token in _AUTH_URL_TOKENS):
            return f"Session is not authenticated — LinkedIn redirected to {page.url}"
        return f"Unexpected URL after navigation: {page.url}"

    return None

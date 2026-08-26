"""
LinkedIn session management.
FILE: automation/session.py

Sessions live in the account's durable Chromium profile directory — there is
no cookie/storage-state snapshotting to the database anymore. ``linkedin_login``
logs in inside the persistent profile (Chromium persists the session to disk
as a side effect), and ``verify_session`` checks whether the profile's session
is still live.
"""
import asyncio
import logging
import random
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from urllib.parse import urlsplit

from patchright.async_api import BrowserContext, Page, Locator, ElementHandle  # noqa: F401

from automation.browser import launch_persistent_browser
from automation.human import (
    random_idle_pause,
    find_and_type_resilient,
    find_and_click_resilient,
)
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)


class LinkedInSessionStatus(str, Enum):
    """LinkedIn account/session status after verification or login."""
    VALID = "valid"  # Session is active and working
    EXPIRED = "expired"  # Wrong credentials, or an already-linked session expired
    VERIFICATION_REQUIRED = "verification_required"  # PIN / email / SMS code
    CHECKPOINT = "checkpoint"  # Security checkpoint/challenge
    CAPTCHA = "captcha"  # Bot-detection challenge the code-entry flow cannot solve
    THROTTLED = "throttled"  # Too many attempts / unusual activity
    TIMEOUT = "timeout"  # Still in-flight or no terminal browser state
    NETWORK_ERROR = "network_error"  # Page-load / navigation / transport failure
    UNKNOWN = "unknown"  # Unknown state


class SessionVerificationResult:
    """Result of session verification with detailed status."""
    def __init__(self, status: LinkedInSessionStatus, url: str, message: str):
        self.status = status
        self.url = url
        self.message = message

    def to_dict(self):
        return {
            "status": self.status.value,
            "url": self.url,
            "message": self.message
        }


async def find_visible_input_by_type(page: Page, input_type: str) -> Locator:
    """
    Self-healing dynamic selector: Finds the first visible input of given type.
    Uses Playwright Locators for stability. Avoids React-generated IDs.
    Returns a Locator object that can be used directly with human_type().
    """
    # Try using Playwright's get_by_role with textbox role
    try:
        if input_type == "email":
            locator = page.get_by_role(
                "textbox", name=re.compile(r"email|phone|username", re.I)
            )
        elif input_type == "password":
            locator = page.get_by_role("textbox", name=re.compile(r"password", re.I))
        else:
            locator = page.get_by_role("textbox")

        count = await locator.count()
        if count > 0:
            # Find the first visible and enabled input
            for i in range(count):
                element = locator.nth(i)
                is_visible = await element.is_visible()
                is_enabled = await element.is_enabled()
                if is_visible and is_enabled:
                    return element
    except Exception:
        pass

    # Fallback: use get_by_label for email/password
    try:
        if input_type == "email":
            locator = page.get_by_label("email", exact=False)
        elif input_type == "password":
            locator = page.get_by_label("password", exact=False)
        else:
            locator = page.locator(f"input[type='{input_type}']")

        count = await locator.count()
        if count > 0:
            for i in range(count):
                element = locator.nth(i)
                is_visible = await element.is_visible()
                is_enabled = await element.is_enabled()
                if is_visible and is_enabled:
                    return element
    except Exception:
        pass

    # Final fallback: a plain CSS locator checked element-by-element via
    # nth(). IMPORTANT: this used to wrap query_selector_all() handles in
    # ``page.locator(...).filter(has=<ElementHandle>)`` — ``filter()`` only
    # accepts a Locator, so Playwright crashed with
    # ``'ElementHandle' object has no attribute '_selector'`` and turned the
    # self-healing path itself into a hard login failure.
    if input_type == "email":
        # LinkedIn A/B-serves the login field as type="email", type="text",
        # or with no type attribute at all — cover all three variants.
        css = "input[type='email'], input[type='text'], input:not([type])"
    else:
        css = f"input[type='{input_type}']"

    base = page.locator(css)
    for i in range(await base.count()):
        element = base.nth(i)
        try:
            # Check if element is physically visible (has positive dimensions)
            box = await element.bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                continue
            # Check if element is not hidden via CSS and is enabled
            if not (await element.is_visible() and await element.is_enabled()):
                continue
            return element
        except Exception:
            continue

    raise ValueError(f"No visible, enabled input of type '{input_type}' found on page")


async def find_visible_button_by_text(page: Page, button_text: str) -> Locator:
    """
    Self-healing dynamic selector: Finds the last visible button containing given text.
    Uses Playwright Locators instead of CSS classes for stability.
    Returns a Locator object that can be used directly with human_click().
    """
    # Try using Playwright's get_by_role with button role
    try:
        locator = page.get_by_role("button", name=button_text)
        # Get all matching buttons
        count = await locator.count()
        if count > 0:
            # Use the last matching button (main sign-in, not social login)
            return locator.nth(count - 1)
    except Exception:
        pass

    # Fallback: get_by_text
    try:
        locator = page.get_by_text(button_text, exact=False)
        count = await locator.count()
        if count > 0:
            # Filter to only buttons
            for i in range(count):
                element = locator.nth(i)
                tag_name = await element.evaluate("el => el.tagName")
                if tag_name == "BUTTON":
                    # Check if visible and enabled
                    is_visible = await element.is_visible()
                    is_enabled = await element.is_enabled()
                    if is_visible and is_enabled:
                        return element
    except Exception:
        pass

    # Final fallback: nth()-indexed button Locators. This used to wrap
    # query_selector_all() handles in ``page.locator("button").filter(
    # has=<ElementHandle>)`` — ``filter()`` only accepts a Locator, so
    # Playwright crashed with ``'ElementHandle' object has no attribute
    # '_selector'`` inside the self-healing path itself.
    base = page.locator("button")
    matching_buttons: list[Locator] = []

    for i in range(await base.count()):
        button = base.nth(i)
        try:
            # Check if element is physically visible (has positive dimensions)
            box = await button.bounding_box()
            if not box or box["width"] <= 0 or box["height"] <= 0:
                continue
            if not (await button.is_visible() and await button.is_enabled()):
                continue
            # Check if button contains the target text
            text_content = await button.text_content()
            if text_content and button_text.lower() in text_content.lower():
                matching_buttons.append(button)
        except Exception:
            continue

    if not matching_buttons:
        raise ValueError(f"No visible, enabled button containing '{button_text}' found on page")

    # Use the last matching button (to target the main sign-in button, not social login)
    return matching_buttons[-1]


# ---------------------------------------------------------------------------
# Login outcome helpers
# ---------------------------------------------------------------------------

# URL fragments that mean the sign-in POST is still in flight. While the
# browser is on one of these, the attempt has neither succeeded nor failed.
LOGIN_SUBMIT_MARKERS = ("/uas/login-submit", "/login-submit", "/uas/openid")

# URL fragments that mean we have definitively LEFT the login surface.
LOGIN_TERMINAL_MARKERS = (
    "/feed",
    "/checkpoint",
    "/challenge",
    "/verify",
    "/mynetwork",
    "/onboarding",
    "/in/",
    "/notifications",
    "/messaging",
    "/preload",
)

SUCCESS_URL_MARKERS = (
    "/feed",
    "/mynetwork",
    "/onboarding",
    "/in/",
    "/notifications",
    "/messaging",
    "/preload",
    "/jobs",
)

CHECKPOINT_URL_MARKERS = (
    "/checkpoint",
    "/challenge",
    "/security-verification",
    "/check/add-phone",
    "/uas/consumer-email-challenge",
    "/uas/ato-challenge",
)

VERIFICATION_URL_MARKERS = (
    "/verify",
    "verification",
    "two-step",
    "two_step",
)

NETWORK_FAILURE_MARKERS = (
    "chrome-error://",
    "chrome-untrusted://",
    "about:neterror",
    "edge://",
)

# LinkedIn's on-page rejection banners (wrong email / wrong password /
# throttling). The TEXT inside is LinkedIn chrome (never user input), so it
# is safe to log and to echo back in the API error detail.
LOGIN_ERROR_SELECTORS = (
    "#error-for-username",
    "#error-for-password",
    "div[role='alert']",
    "#artdeco-global-alerts .artdeco-inline-feedback--error",
    ".login__form .alert",
    ".form__error",
)

# Human-verification challenges embedded in the login page. IMPORTANT: these
# must NOT be classified as VERIFICATION_REQUIRED — the pending-session flow
# can only type a 6-digit code, it cannot solve a CAPTCHA, so routing a
# captcha page into it would strand the user in an unwinnable session.
CAPTCHA_SELECTORS = (
    "iframe[src*='arkose']",
    "iframe[src*='captcha' i]",
    "iframe[title*='captcha' i]",
    "#captcha-internal",
    "div[data-captcha]",
)

LOGGED_IN_SELECTORS = (
    "[data-control-name='nav.home']",
    ".global-nav",
    "#global-nav",
    ".feed-identity-module",
    "a[href*='/feed/']",
    "[data-test-global-nav-link]",
)

CHECKPOINT_UI_SELECTORS = (
    "input[autocomplete='one-time-code']",
    "input[name='pin']",
    "input[id*='verification']",
    "#input__email_verification_pin",
    "input[name='challengeId']",
    "input[id*='challenge']",
)

LOGIN_FORM_SELECTORS = (
    "input[type='password']",
    "#password",
    "#username",
    "form.login__form",
    "form[action*='login']",
)

THROTTLE_BANNER_HINTS = (
    "too many",
    "try again later",
    "unusual activity",
    "temporarily restricted",
    "we've temporarily",
    "we have temporarily",
    "rate limit",
)

_SENSITIVE_QUERY_RE = re.compile(
    r"([?&])(token|access_token|refresh_token|password|passwd|sessionid|li_at|code|otp)="
    r"[^&\s#]+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LoginPageSnapshot:
    """Browser evidence used to classify a LinkedIn login attempt.

    Never includes passwords, cookies, or raw query strings.
    """

    url: str
    title: str = ""
    rejection_banner: Optional[str] = None
    captcha: bool = False
    logged_in_surface: bool = False
    checkpoint_ui: bool = False
    login_form_visible: bool = False
    navigation_in_flight: bool = False
    network_failure: Optional[str] = None


@dataclass(frozen=True)
class LoginOutcome:
    """Classified result of a LinkedIn login attempt."""

    status: LinkedInSessionStatus
    error_detail: Optional[str]
    keep_session: bool


def sanitized_url_path(url: str) -> str:
    """URL with query string stripped — safe to log (challenge tokens live in the query)."""
    if not url:
        return "(unparseable URL)"
    try:
        parts = urlsplit(url)
        if not (parts.scheme and parts.netloc):
            return parts.path or "(unparseable URL)"
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    except Exception:
        return "(unparseable URL)"


def sanitize_exception_message(exc: BaseException | str) -> str:
    """Redact tokens / query strings from a Playwright or network error."""
    text = str(exc) if not isinstance(exc, str) else exc
    text = _SENSITIVE_QUERY_RE.sub(r"\1\2=***", text)
    text = re.sub(r"\?[^\s]+", "", text)
    return text[:300]


def _url_matches(url: str, markers: tuple[str, ...]) -> bool:
    lowered = (url or "").lower()
    return any(marker in lowered for marker in markers)


def is_login_surface_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "/login" in lowered or "/uas/login" in lowered


def is_success_url(url: str) -> bool:
    if is_login_surface_url(url):
        return False
    lowered = (url or "").lower()
    if _url_matches(lowered, SUCCESS_URL_MARKERS):
        return True
    # Bare linkedin.com/ after a successful sign-in is a real terminal state.
    try:
        parts = urlsplit(url or "")
    except Exception:
        return False
    host = (parts.netloc or "").lower()
    path = (parts.path or "").rstrip("/")
    return host.endswith("linkedin.com") and path in ("", "/")


def is_checkpoint_url(url: str) -> bool:
    return _url_matches(url, CHECKPOINT_URL_MARKERS)


def is_verification_url(url: str) -> bool:
    return _url_matches(url, VERIFICATION_URL_MARKERS)


def is_network_failure_url(url: str) -> bool:
    return _url_matches(url, NETWORK_FAILURE_MARKERS)


def is_throttle_banner(text: Optional[str]) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(hint in lowered for hint in THROTTLE_BANNER_HINTS)


def decide_login_outcome(snapshot: LoginPageSnapshot) -> LoginOutcome:
    """Classify a gathered page snapshot. Pure — safe to unit-test.

    A still-visible ``/login`` URL without a rejection banner is NOT a
    credential failure: LinkedIn's post-submit redirect is often slow, and
    treating that as ``EXPIRED`` produced the production 400s.
    """
    url = snapshot.url or ""

    if snapshot.network_failure or is_network_failure_url(url):
        detail = snapshot.network_failure or (
            "Could not load LinkedIn (network or page-load failure)."
        )
        return LoginOutcome(LinkedInSessionStatus.NETWORK_ERROR, detail, False)

    if snapshot.logged_in_surface or is_success_url(url):
        return LoginOutcome(LinkedInSessionStatus.VALID, None, True)

    if snapshot.captcha:
        return LoginOutcome(
            LinkedInSessionStatus.CAPTCHA,
            "LinkedIn presented a CAPTCHA on the login page — bot-detection "
            "flag on this IP/browser profile. Complete the challenge in a "
            "normal browser, then retry.",
            False,
        )

    if is_verification_url(url) or (
        snapshot.checkpoint_ui and not is_checkpoint_url(url)
    ):
        return LoginOutcome(
            LinkedInSessionStatus.VERIFICATION_REQUIRED,
            "LinkedIn requires a verification code before this login can finish.",
            True,
        )

    if is_checkpoint_url(url) or snapshot.checkpoint_ui:
        return LoginOutcome(
            LinkedInSessionStatus.CHECKPOINT,
            "LinkedIn opened a security checkpoint. Complete the challenge "
            "to finish connecting this account.",
            True,
        )

    if snapshot.rejection_banner:
        banner = snapshot.rejection_banner
        if is_throttle_banner(banner):
            return LoginOutcome(
                LinkedInSessionStatus.THROTTLED,
                f"LinkedIn is throttling sign-in attempts: {banner}",
                False,
            )
        return LoginOutcome(
            LinkedInSessionStatus.EXPIRED,
            f"LinkedIn rejected the sign-in: {banner}",
            False,
        )

    if snapshot.navigation_in_flight or _url_matches(url, LOGIN_SUBMIT_MARKERS):
        return LoginOutcome(
            LinkedInSessionStatus.TIMEOUT,
            "LinkedIn is still processing the sign-in (slow redirect). "
            "This is not a confirmed credential error — please retry.",
            False,
        )

    if is_login_surface_url(url):
        if snapshot.login_form_visible:
            return LoginOutcome(
                LinkedInSessionStatus.TIMEOUT,
                "LinkedIn stayed on the login page without accepting or "
                "rejecting the form. This is usually a slow redirect, a "
                "blocked request, or an unrecognized layout — not a "
                "confirmed credential error.",
                False,
            )
        return LoginOutcome(
            LinkedInSessionStatus.TIMEOUT,
            "LinkedIn left the login form but never reached a finished "
            "page (feed, checkpoint, or error). The redirect was still "
            "in flight — not a confirmed credential error.",
            False,
        )

    return LoginOutcome(
        LinkedInSessionStatus.UNKNOWN,
        f"LinkedIn login ended on an unrecognized page ({sanitized_url_path(url)}).",
        False,
    )


async def _any_visible(page: Page, selectors: tuple[str, ...]) -> bool:
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible():
                return True
        except Exception:
            continue
    return False


async def extract_login_error(page: Page) -> Optional[str]:
    """
    Return the text of LinkedIn's on-page login rejection banner, if visible.

    LinkedIn bounces a failed sign-in straight back to /login with an inline
    error (e.g. "Wrong email or password..."). Old code only looked at the
    URL, so operators got "still on login page" with zero indication of why.
    """
    for selector in LOGIN_ERROR_SELECTORS:
        try:
            banner = page.locator(selector).first
            if await banner.is_visible():
                text = (await banner.text_content() or "").strip()
                text = " ".join(text.split())  # collapse whitespace/newlines
                if text:
                    return text[:300]
        except Exception:
            continue
    return None


async def detect_human_challenge(page: Page) -> bool:
    """True when a CAPTCHA-type iframe block is visible on the current page."""
    return await _any_visible(page, CAPTCHA_SELECTORS)


async def collect_login_snapshot(page: Page) -> LoginPageSnapshot:
    """Read the current page once. Never logs or returns secrets."""
    url = page.url or ""
    title = ""
    try:
        title = await page.title()
    except Exception:
        title = ""
    return LoginPageSnapshot(
        url=url,
        title=title or "",
        rejection_banner=await extract_login_error(page),
        captcha=await detect_human_challenge(page),
        logged_in_surface=await _any_visible(page, LOGGED_IN_SELECTORS),
        checkpoint_ui=await _any_visible(page, CHECKPOINT_UI_SELECTORS),
        login_form_visible=await _any_visible(page, LOGIN_FORM_SELECTORS),
        navigation_in_flight=_url_matches(url, LOGIN_SUBMIT_MARKERS),
        network_failure=(
            "LinkedIn page failed to load." if is_network_failure_url(url) else None
        ),
    )


def login_state_is_terminal(snapshot: LoginPageSnapshot) -> bool:
    """True when waiting longer cannot change the classification."""
    if snapshot.network_failure or is_network_failure_url(snapshot.url):
        return True
    if snapshot.logged_in_surface or is_success_url(snapshot.url):
        return True
    if snapshot.captcha:
        return True
    if snapshot.rejection_banner:
        return True
    if is_checkpoint_url(snapshot.url) or is_verification_url(snapshot.url):
        return True
    if snapshot.checkpoint_ui:
        return True
    if snapshot.navigation_in_flight or _url_matches(snapshot.url, LOGIN_SUBMIT_MARKERS):
        return False
    if is_login_surface_url(snapshot.url):
        return False
    # Left /login via some other path — treat as terminal so the classifier
    # can inspect the new surface.
    return True


async def wait_for_login_outcome(page: Page, timeout_ms: int = 45000) -> LoginPageSnapshot:
    """
    Poll until the post-submit navigation reaches a *terminal* browser state.

    Terminal means: logged-in surface, rejection banner, CAPTCHA, checkpoint /
    verification UI, a non-login URL, or a network-error page. A still-visible
    ``/login`` URL with no banner is *not* terminal — that is the slow
    in-flight redirect that used to be misclassified as bad credentials.

    The deadline is a safety bound so the request cannot hang past the
    reverse-proxy timeout. The caller then classifies the last snapshot.
    """
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    snapshot = await collect_login_snapshot(page)
    while True:
        if login_state_is_terminal(snapshot):
            return snapshot
        if asyncio.get_running_loop().time() >= deadline:
            return snapshot
        await page.wait_for_timeout(400)
        snapshot = await collect_login_snapshot(page)


async def uncheck_all_checkboxes(page: Page, context_label: str = "login") -> None:
    """
    Uncheck every visible checkbox on the current page ("Keep me signed in"
    on the login form, "Remember this device" on the verification page).

    This replaced the query_selector_all()-based version which crashed on
    every production run with ``'ElementHandle' object has no attribute
    'wait_for'`` (ElementHandle does not expose wait_for — only Locator
    does). It also deduplicated with ``set()`` over wrapper objects, so the
    SAME box found by two selectors was processed twice.
    """
    try:
        await page.wait_for_timeout(500)
        boxes = page.locator("input[type='checkbox'], [role='checkbox']")
        total = await boxes.count()
        logger.info(f"🔍 Found {total} total checkbox(es) on the {context_label} page")

        for i in range(total):
            checkbox = boxes.nth(i)
            try:
                if not await checkbox.is_visible():
                    continue
                if not await checkbox.is_checked():
                    continue
                try:
                    await checkbox.uncheck(force=True, timeout=4000)
                except Exception:
                    await checkbox.click(force=True, timeout=4000)
                # Verify it actually unticked; JS fallback for stubborn boxes.
                await page.wait_for_timeout(200)
                if await checkbox.is_checked():
                    try:
                        await checkbox.evaluate("el => el.checked = false")
                    except Exception:
                        logger.warning(
                            f"⚠️ Could not uncheck checkbox {i} on the {context_label} page"
                        )
            except Exception as e:
                logger.warning(f"⚠️ Could not process checkbox {i}: {str(e)}")
    except Exception as e:
        logger.warning(f"⚠️ Could not uncheck checkboxes on the {context_label} page: {str(e)}")


async def linkedin_login(email: str, password: str, account, keep_alive: bool = False) -> tuple[LinkedInSessionStatus, any, Optional[str]]:
    """
    Performs LinkedIn login inside the account's PERSISTENT browser profile
    and returns the session status.

    The login happens in the account's durable Chromium user-data-dir via
    launch_persistent_browser(); once LinkedIn sets its session cookies,
    Chromium persists them to disk automatically — there is no explicit
    "save session state" step anymore.

    Args:
        email: LinkedIn email
        password: LinkedIn password
        account: LinkedInAccount object (must exist; its profile_dir is used)
        keep_alive: If True and LinkedIn demands verification, keeps browser
            session alive for the verification-code flow (returns session
            resources the caller owns and must close).

    Returns:
        tuple: (LinkedInSessionStatus, session_resources or None, error_detail or None)
            - session_resources (when returned) is: (pw, browser, context, page, user_agent)
              where `browser` is always None for persistent contexts.
            - error_detail is LinkedIn's own on-page rejection text (or a
              captcha note) when one was detected — safe to surface to the
              caller in an HTTP error response.

    Returns LinkedInSessionStatus: VALID, EXPIRED, CHECKPOINT, VERIFICATION_REQUIRED, or UNKNOWN.
    """
    pw, browser, context, page = await launch_persistent_browser(account, headless=True)
    actual_user_agent = account.user_agent  # pinned at launch, stable for this account

    # Tracks whether we're handing live browser resources back to the caller
    # (keep_alive flow). If None at exit, the finally block closes everything —
    # this also prevents browser leaks when an exception is raised mid-login.
    handed_off_resources = None

    # Define multi-selector fallback pools to counter LinkedIn A/B testing
    USERNAME_SELECTORS = [
        "input[type='email']:nth-of-type(1)",
        "input[type='email']",
        "#username",
        "#session_key",
        "input[name='session_key']",
        "input[autocomplete='username']",
        "input[type='text']",
        "input[id*='username']",
        "input[aria-label*='email']",
        "input[aria-label*='Email']",
        "input[placeholder*='Email or phone']",
        "input[placeholder*='email']",
        "//input[@placeholder='Email or phone']",
        "//input[contains(@placeholder, 'email')]",
        "//input[contains(@placeholder, 'phone')]",
        "xpath=//input[@placeholder='Email or phone']",
    ]

    PASSWORD_SELECTORS = [
        "input[type='password']:nth-of-type(1)",
        "input[type='password']",
        "#password",
        "#session_password",
        "input[name='session_password']",
        "input[id*='password']",
        "input[placeholder*='Password']",
        "input[placeholder*='password']",
    ]

    SUBMIT_SELECTORS = [
        "button[type='submit']",
        "button[aria-label='Sign in']",
        ".btn__primary--large",
        "form.login__form button",
        "button._8de5873b",
        "button:has-text('Sign in')",
        "button[type='button']:has-text('Sign in')",
        "button[text='Sign in']",
    ]

    try:

        # ── Step 1: Navigate to LinkedIn login page ───────────────────────────
        logger.info("🌐 Navigating to LinkedIn Login Portal...")
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=30000)
        # await page.wait_for_load_state("networkidle", timeout=120000)
        await random_idle_pause(2, 4)
        # The login form is hydrated by client-side JS after domcontentloaded.
        # Give it a bounded chance to appear ONCE so the per-selector probes
        # below don't all time out against a half-painted page.
        try:
            await page.wait_for_selector("input", state="attached", timeout=10000)
        except Exception:
            logger.warning("⚠️ No <input> appeared within 10s of page load — page may not have rendered")
        if should_take_screenshots():
            await page.screenshot(path="login_page_debug.png", full_page=True)

        # Debug: Log all input elements on the page (development only)
        inputs = await page.query_selector_all("input")
        if should_log_debug():
            logger.debug(f"Found {len(inputs)} input elements on the page")
            for i, inp in enumerate(inputs):
                attrs = await inp.evaluate("el => ({id: el.id, name: el.name, type: el.type, placeholder: el.placeholder, class: el.className})")
                logger.debug(f"Input {i}: {attrs}")

            # Debug: Log all button elements on the page
            buttons = await page.query_selector_all("button")
            logger.debug(f"Found {len(buttons)} button elements on the page")
            for i, btn in enumerate(buttons):
                attrs = await btn.evaluate("el => ({id: el.id, type: el.type, text: el.textContent, class: el.className})")
                logger.debug(f"Button {i}: {attrs}")

        if len(inputs) == 0:
            raise ValueError(f"No input elements found on page. URL: {page.url}. Page may not have loaded correctly.")

        # ── Step 2: Fill in email ─────────────────────────────────────────────
        # Prefer visible role/label locators (they match LinkedIn's current
        # A/B layouts instantly). Only then walk the CSS fallback pool, and
        # do it with a 0ms probe so a loaded form never burns 1.5s per miss.
        logger.info("✍️ Typing email credential using role/label first...")
        try:
            email_locator = await find_visible_input_by_type(page, "email")
            await find_and_type_resilient(page, [email_locator], email, "Email Field")
        except Exception:
            logger.warning("Role/label email lookup missed — trying CSS fallback pool")
            await find_and_type_resilient(
                page, USERNAME_SELECTORS, email, "Email Field", probe_timeout_ms=0
            )
        await random_idle_pause(0.5, 1.5)

        # ── Step 3: Fill in password ──────────────────────────────────────────
        logger.info("🔑 Typing password credential using role/label first...")
        try:
            password_locator = await find_visible_input_by_type(page, "password")
            await find_and_type_resilient(page, [password_locator], password, "Password Field")
        except Exception:
            logger.warning("Role/label password lookup missed — trying CSS fallback pool")
            await find_and_type_resilient(
                page, PASSWORD_SELECTORS, password, "Password Field", probe_timeout_ms=0
            )
        await random_idle_pause(0.8, 2.0)

        # ── Step 3.5: Uncheck all checkboxes BEFORE clicking submit ─────────────
        logger.info("🔲 Unchecking all checkboxes to avoid LinkedIn emails...")
        await uncheck_all_checkboxes(page, context_label="login")

        # ── Step 4: Click Sign In ─────────────────────────────────────────────
        logger.info("🚀 Clicking submit button...")
        try:
            submit_locator = await find_visible_button_by_text(page, "Sign in")
            await find_and_click_resilient(page, [submit_locator], "Sign In Button")
        except Exception:
            logger.warning("Role/label submit lookup missed — trying CSS fallback pool")
            await find_and_click_resilient(
                page, SUBMIT_SELECTORS, "Sign In Button", probe_timeout_ms=0
            )

        # ── Step 5: Wait for a real terminal browser state ────────────────────
        snapshot = await wait_for_login_outcome(page, timeout_ms=45000)
        outcome = decide_login_outcome(snapshot)

        logger.info(
            "📍 LinkedIn login outcome status=%s url=%s rejection_banner=%s captcha=%s "
            "logged_in=%s checkpoint_ui=%s form_visible=%s in_flight=%s",
            outcome.status.value,
            sanitized_url_path(snapshot.url),
            snapshot.rejection_banner or "(none)",
            snapshot.captcha,
            snapshot.logged_in_surface,
            snapshot.checkpoint_ui,
            snapshot.login_form_visible,
            snapshot.navigation_in_flight,
        )

        if should_take_screenshots() and outcome.status != LinkedInSessionStatus.VALID:
            try:
                await page.screenshot(path="login_failure_diagnostics.png", full_page=True)
                with open("login_failure_diagnostics.html", "w", encoding="utf-8") as fh:
                    fh.write(await page.content())
            except Exception:
                pass

        keep_browser = bool(
            keep_alive
            and outcome.keep_session
            and outcome.status
            in (
                LinkedInSessionStatus.VALID,
                LinkedInSessionStatus.VERIFICATION_REQUIRED,
                LinkedInSessionStatus.CHECKPOINT,
            )
        )
        if outcome.status == LinkedInSessionStatus.VALID:
            handed_off_resources = (pw, browser, context, page, actual_user_agent)
            return (LinkedInSessionStatus.VALID, handed_off_resources, None)

        if keep_browser:
            # Checkpoint / verification: leave the persistent profile open so
            # the user can submit the code. CAPTCHA is deliberately excluded.
            handed_off_resources = (pw, browser, context, page, actual_user_agent)
            status = (
                LinkedInSessionStatus.VERIFICATION_REQUIRED
                if outcome.status
                in (
                    LinkedInSessionStatus.CHECKPOINT,
                    LinkedInSessionStatus.VERIFICATION_REQUIRED,
                )
                else outcome.status
            )
            return (status, handed_off_resources, outcome.error_detail)

        return (outcome.status, None, outcome.error_detail)

    except Exception as e:
        if should_take_screenshots():
            try:
                await page.screenshot(path=f"error_screenshot_{random.randint(1000, 9999)}.png", full_page=True)
            except Exception:
                pass
        detail = sanitize_exception_message(e)
        logger.error("❌ Automation Error Encountered: %s", detail)
        lowered = detail.lower()
        networkish = any(
            token in lowered
            for token in (
                "net::",
                "timeout",
                "timed out",
                "err_connection",
                "err_name_not_resolved",
                "err_tunnel",
                "err_proxy",
                "err_internet",
                "navigation",
                "page.goto",
            )
        )
        status = (
            LinkedInSessionStatus.NETWORK_ERROR
            if networkish
            else LinkedInSessionStatus.UNKNOWN
        )
        return (status, None, detail or "LinkedIn login failed before a page state could be read.")
    finally:
        # Close everything UNLESS we handed live resources to the caller for
        # the keep_alive verification flow. (Also fixes the old leak where an
        # exception with keep_alive=True left the browser running forever.)
        if handed_off_resources is None:
            logger.info("🧹 Closing browser instance contexts...")
            try:
                await context.close()
            except Exception:
                pass
            try:
                await pw.stop()
            except Exception:
                pass


async def verify_session(page: Page) -> SessionVerificationResult:
    """
    Navigates to LinkedIn feed to check if the loaded session is still valid.
    Returns SessionVerificationResult with detailed status.
    """
    logger.info("🔍 Navigating to LinkedIn feed to verify session...")
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
    # URL may contain challenge tokens — only log it in debug mode.
    if should_log_debug():
        logger.debug(f"Current URL after navigation: {page.url}")

    # Take screenshot for debugging (development only)
    if should_take_screenshots():
        await page.screenshot(path="session_verification_debug.png", full_page=True)

    # Log page title for additional debugging (development only)
    if should_log_debug():
        try:
            page_title = await page.title()
            logger.debug(f"Page title: {page_title}")
        except:
            pass

    # Check for ID verification page
    if "/checkpoint/challenge" in page.url or "/checkpoint" in page.url:
        logger.warning("⚠️ LinkedIn requires ID verification or security checkpoint")
        # Check if it's a code-based verification or document upload
        page_content = await page.content()
        if "code" in page_content.lower() or "otp" in page_content.lower():
            return SessionVerificationResult(
                status=LinkedInSessionStatus.CHECKPOINT,
                url=page.url,
                message="LinkedIn requires verification code (SMS/email)"
            )
        else:
            return SessionVerificationResult(
                status=LinkedInSessionStatus.CHECKPOINT,
                url=page.url,
                message="LinkedIn requires ID document verification (manual intervention needed)"
            )

    # If redirected to login page, session expired
    if "/login" in page.url or "/uas/login" in page.url:
        logger.warning("⚠️ Session expired - redirected to login page")
        return SessionVerificationResult(
            status=LinkedInSessionStatus.EXPIRED,
            url=page.url,
            message="Session expired - redirected to login page"
        )

    # Check for verification required page
    if "/verify" in page.url or "verification" in page.url.lower():
        logger.warning("⚠️ LinkedIn requires account verification")
        return SessionVerificationResult(
            status=LinkedInSessionStatus.VERIFICATION_REQUIRED,
            url=page.url,
            message="LinkedIn requires account verification"
        )

    # Check if we're on feed page (valid session)
    if "/feed" in page.url:
        logger.info("✅ Session valid - on feed page")
        return SessionVerificationResult(
            status=LinkedInSessionStatus.VALID,
            url=page.url,
            message="Session is valid and active"
        )

    # Check for feed-specific element (valid session)
    try:
        await page.wait_for_selector("[data-control-name='nav.home']", timeout=3000)
        logger.info("✅ Session valid - found home navigation element")
        return SessionVerificationResult(
            status=LinkedInSessionStatus.VALID,
            url=page.url,
            message="Session is valid and active"
        )
    except Exception:
        # Try alternate feed indicator
        try:
            await page.wait_for_selector(".feed-identity-module", timeout=3000)
            logger.info("✅ Session valid - found feed identity module")
            return SessionVerificationResult(
                status=LinkedInSessionStatus.VALID,
                url=page.url,
                message="Session is valid and active"
            )
        except Exception:
            # Try checking for any LinkedIn navigation element
            try:
                await page.wait_for_selector(".global-nav", timeout=3000)
                logger.info("✅ Session valid - found global navigation")
                return SessionVerificationResult(
                    status=LinkedInSessionStatus.VALID,
                    url=page.url,
                    message="Session is valid and active"
                )
            except Exception:
                logger.warning(f"⚠️ Session state unknown - URL: {page.url}")
                # Log page title for debugging
                try:
                    page_title = await page.title()
                    logger.warning(f"⚠️ Page title: {page_title}")
                except:
                    pass
                return SessionVerificationResult(
                    status=LinkedInSessionStatus.UNKNOWN,
                    url=page.url,
                    message="Could not determine session status - unknown page state"
                )


# ---------------------------------------------------------------------------
# Cookie-based session import (no password, no sign-in form)
# ---------------------------------------------------------------------------


async def linkedin_login_with_cookies(
    cookies: list[dict],
    account,
    keep_alive: bool = False,
) -> tuple[LinkedInSessionStatus, any, Optional[str]]:
    """Adopt an existing LinkedIn session by injecting its cookies.

    This is the datacenter-IP alternative to ``linkedin_login``. Instead of
    driving LinkedIn's sign-in form from the server — which from a hosted IP
    very often returns a CAPTCHA or ``/checkpoint/challenge`` — the user signs
    in from their own browser and hands us the resulting ``li_at`` cookie.
    No password is submitted, and no password needs to be stored.

    The cookies are written into the account's DURABLE profile directory, so
    Chromium persists them to disk exactly like a real login: later campaign
    sessions, feed scrolls and verification runs just reopen the profile.

    Note this does not disguise the egress IP. LinkedIn can still challenge a
    session that was created on a residential connection and is then used from
    a datacenter, so a per-account sticky proxy remains the real fix.

    Mirrors ``linkedin_login``'s return contract so callers can treat the two
    paths identically.

    Args:
        cookies: Playwright cookie dicts from
            ``automation.cookie_import.parse_cookie_input``.
        account: LinkedInAccount whose ``profile_dir`` receives the session.
        keep_alive: When True, hand the live browser back to the caller on a
            checkpoint so the existing verification-code flow can drive it.

    Returns:
        (LinkedInSessionStatus, session_resources or None, error_detail or None)
    """
    pw, browser, context, page = await launch_persistent_browser(account, headless=True)
    actual_user_agent = account.user_agent

    # Set to the resource tuple whenever we hand the LIVE browser back to the
    # caller (VALID, or a checkpoint under keep_alive). While it is None the
    # finally block owns the browser and must close it, so no code path can
    # leak a Chromium process holding the account's profile lock.
    handed_off_resources = None

    try:
        logger.info(
            "🍪 Injecting %d LinkedIn cookie(s) into profile %s",
            len(cookies),
            account.profile_dir,
        )
        # Start from a clean slate: a stale li_at left over from an earlier
        # import would otherwise win and the user would see the OLD account.
        try:
            await context.clear_cookies()
        except Exception:
            logger.debug("Could not clear existing cookies (continuing)", exc_info=True)

        await context.add_cookies(cookies)

        # verify_session() navigates to /feed and classifies the outcome —
        # reuse it so cookie import and password login report identically.
        result = await verify_session(page)
        logger.info(
            "🍪 Cookie import verification: %s — %s",
            result.status.value,
            result.message,
        )

        if result.status == LinkedInSessionStatus.VALID:
            handed_off_resources = (pw, browser, context, page, actual_user_agent)
            return LinkedInSessionStatus.VALID, handed_off_resources, None

        # An imported cookie that lands on /login was already dead when it was
        # copied (or was revoked in the meantime). Say so precisely — "wrong
        # password" would be nonsense here since no password was used.
        if result.status == LinkedInSessionStatus.EXPIRED:
            return (
                LinkedInSessionStatus.EXPIRED,
                None,
                "LinkedIn rejected the imported session cookie. It has expired or "
                "was revoked — sign in to LinkedIn again in your browser, copy a "
                "fresh li_at cookie and retry. Staying signed in on that browser "
                "keeps the session alive longer.",
            )

        if result.status in (
            LinkedInSessionStatus.CHECKPOINT,
            LinkedInSessionStatus.VERIFICATION_REQUIRED,
        ):
            detail = (
                "LinkedIn asked this session to re-verify from our server. This "
                "usually means it noticed the session moved to a different "
                "network. Complete the challenge, or assign this account a "
                "sticky proxy so it always browses from one IP."
            )
            if keep_alive:
                handed_off_resources = (pw, browser, context, page, actual_user_agent)
                return (
                    LinkedInSessionStatus.VERIFICATION_REQUIRED,
                    handed_off_resources,
                    detail,
                )
            return result.status, None, detail

        return (
            result.status,
            None,
            f"Could not confirm the imported session: {result.message}",
        )

    except Exception as exc:
        logger.error("❌ Cookie import failed: %s", exc, exc_info=True)
        return (
            LinkedInSessionStatus.UNKNOWN,
            None,
            f"Could not apply the session cookie: {exc}",
        )

    finally:
        # Close the browser unless it was handed to the caller. The cookies
        # are already flushed to the profile directory on disk by Chromium, so
        # closing here never loses the imported session.
        if handed_off_resources is None:
            try:
                await context.close()
            except Exception:
                logger.debug("Error closing context after cookie import", exc_info=True)
            try:
                await pw.stop()
            except Exception:
                logger.debug("Error stopping playwright after cookie import", exc_info=True)

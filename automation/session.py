"""
LinkedIn session management.
FILE: automation/session.py

Sessions live in the account's durable Chromium profile directory — there is
no cookie/storage-state snapshotting to the database anymore. ``linkedin_login``
logs in inside the persistent profile (Chromium persists the session to disk
as a side effect), and ``verify_session`` checks whether the profile's session
is still live.
"""
import logging
import random
from enum import Enum

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
    """LinkedIn account/session status after verification."""
    VALID = "valid"  # Session is active and working
    EXPIRED = "expired"  # Session expired, needs re-login
    VERIFICATION_REQUIRED = "verification_required"  # ID verification needed
    CHECKPOINT = "checkpoint"  # Security checkpoint/challenge
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
            locator = page.get_by_role("textbox", name="email")
        elif input_type == "password":
            locator = page.get_by_role("textbox", name="password")
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


async def linkedin_login(email: str, password: str, account, keep_alive: bool = False) -> tuple[LinkedInSessionStatus, any]:
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
        tuple: (LinkedInSessionStatus, session_resources or None)
            - session_resources (when returned) is: (pw, browser, context, page, user_agent)
              where `browser` is always None for persistent contexts.

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
        logger.info("✍️ Typing email credential using fallback matching...")
        try:
            await find_and_type_resilient(page, USERNAME_SELECTORS, email, "Email Field")
        except Exception:
            logger.warning("Standard selectors failed, activating self-healing dynamic selector...")
            email_locator = await find_visible_input_by_type(page, "email")
            logger.info(f"Dynamic locator found for Email field")
            await find_and_type_resilient(page, [email_locator], email, "Email Field")
        await random_idle_pause(0.5, 1.5)

        # ── Step 3: Fill in password ──────────────────────────────────────────
        logger.info("🔑 Typing password credential using fallback matching...")
        try:
            await find_and_type_resilient(page, PASSWORD_SELECTORS, password, "Password Field")
        except Exception:
            logger.warning("Standard selectors failed, activating self-healing dynamic selector...")
            password_locator = await find_visible_input_by_type(page, "password")
            logger.info(f"Dynamic locator found for Password field")
            await find_and_type_resilient(page, [password_locator], password, "Password Field")
        await random_idle_pause(0.8, 2.0)

        # ── Step 3.5: Uncheck all checkboxes BEFORE clicking submit ─────────────
        logger.info("🔲 Unchecking all checkboxes to avoid LinkedIn emails...")
        try:
            # Wait a moment for any dynamic checkboxes to load
            await page.wait_for_timeout(500)

            # Try multiple selector strategies for LinkedIn's "Keep me signed in" checkbox
            checkbox_selectors = [
                "input[type='checkbox']",
                "input[name='rememberMe']",
                "input[id*='remember']",
                "input[id*='Remember']",
                "[role='checkbox']",
                ".checkbox__input",
                ".remember-me-checkbox",
            ]

            checkboxes_found = []

            for selector in checkbox_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        checkboxes_found.extend(elements)
                        logger.debug(f"Found {len(elements)} checkboxes with selector: {selector}")
                except:
                    pass

            # Remove duplicates
            unique_checkboxes = list(set(checkboxes_found))
            logger.info(f"🔍 Found {len(unique_checkboxes)} total checkbox(es) on the page")

            for i, checkbox in enumerate(unique_checkboxes):
                try:
                    # Force check visibility with a small wait
                    await checkbox.wait_for(state="visible", timeout=1000)
                    is_checked = await checkbox.is_checked()
                    logger.info(f"Checkbox {i}: checked={is_checked}")

                    if is_checked:
                        # Try multiple methods to uncheck
                        try:
                            await checkbox.click(force=True)
                            logger.info(f"✅ Unchecked checkbox {i} via click")
                        except:
                            try:
                                await checkbox.uncheck(force=True)
                                logger.info(f"✅ Unchecked checkbox {i} via uncheck")
                            except:
                                logger.warning(f"⚠️ Could not uncheck checkbox {i}")

                            # Verify it's unchecked
                            await page.wait_for_timeout(200)
                            if await checkbox.is_checked():
                                logger.warning(f"⚠️ Checkbox {i} still checked after uncheck attempt")
                                # Try one more time with JavaScript
                                try:
                                    await checkbox.evaluate("el => el.checked = false")
                                    logger.info(f"✅ Force unchecked checkbox {i} via JavaScript")
                                except:
                                    logger.warning(f"⚠️ JavaScript uncheck also failed for checkbox {i}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not process checkbox {i}: {str(e)}")
        except Exception as e:
            logger.warning(f"⚠️ Could not uncheck checkboxes: {str(e)}")

        # ── Step 4: Click Sign In ─────────────────────────────────────────────
        logger.info("🚀 Clicking submit button...")
        try:
            await find_and_click_resilient(page, SUBMIT_SELECTORS, "Sign In Button")
        except Exception:
            logger.warning("Standard selectors failed, activating self-healing dynamic selector...")
            submit_locator = await find_visible_button_by_text(page, "Sign in")
            logger.info(f"Dynamic locator found for Sign In button")
            await find_and_click_resilient(page, [submit_locator], "Sign In Button")
        await random_idle_pause(2, 4)  # Wait for redirect + page load

        # ── Step 5: Verify login result and determine status ───────────────────
        # URL may contain challenge tokens — only log it in debug mode.
        if should_log_debug():
            logger.debug(f"📍 Current URL after login attempt: {page.url}")

        # Check for checkpoint/bot detection
        if "/checkpoint" in page.url or "/checkpoint/challenge" in page.url:
            logger.warning("⚠️ LinkedIn security checkpoint detected - possible bot detection")
            if should_take_screenshots():
                await page.screenshot(path="checkpoint_detected.png", full_page=True)
            if keep_alive:
                # Keep session alive for verification (resources handed to caller)
                handed_off_resources = (pw, browser, context, page, actual_user_agent)
                return (LinkedInSessionStatus.VERIFICATION_REQUIRED, handed_off_resources)
            return (LinkedInSessionStatus.CHECKPOINT, None)

        # Check for verification required
        if "/verify" in page.url or "verification" in page.url.lower():
            logger.warning("⚠️ LinkedIn requires account verification")
            if should_take_screenshots():
                await page.screenshot(path="verification_required.png", full_page=True)
            if keep_alive:
                # Keep session alive for verification (resources handed to caller)
                handed_off_resources = (pw, browser, context, page, actual_user_agent)
                return (LinkedInSessionStatus.VERIFICATION_REQUIRED, handed_off_resources)
            return (LinkedInSessionStatus.VERIFICATION_REQUIRED, None)

        # Check if still on login page (failed login)
        if "/login" in page.url or "/uas/login" in page.url:
            logger.error("❌ Login failed - still on login page")
            if should_take_screenshots():
                await page.screenshot(path="login_failure_diagnostics.png", full_page=True)
            return (LinkedInSessionStatus.EXPIRED, None)

        # Check if on feed page (successful login)
        if "/feed" in page.url:
            logger.info("✅ Login successful - on feed page")
            # No explicit save needed: the session now lives in the persistent
            # profile directory; Chromium persisted it to disk automatically.
            handed_off_resources = (pw, browser, context, page, actual_user_agent)
            return (LinkedInSessionStatus.VALID, handed_off_resources)

        # Unknown state
        logger.warning("⚠️ Unknown page state after login")
        if should_take_screenshots():
            await page.screenshot(path="unknown_state.png", full_page=True)
        return (LinkedInSessionStatus.UNKNOWN, None)

    except Exception as e:
        if should_take_screenshots():
            try:
                await page.screenshot(path=f"error_screenshot_{random.randint(1000, 9999)}.png", full_page=True)
            except Exception:
                pass
        logger.error(f"❌ Automation Error Encountered: {str(e)}")
        return (LinkedInSessionStatus.EXPIRED, None)
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

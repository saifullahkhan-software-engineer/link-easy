"""
LinkedIn session cookie management.
FILE: automation/session.py
 
Saves cookies after login, loads them before each run,
and verifies the session is still active.
"""
import json
import logging
import random
from datetime import datetime, timezone
from enum import Enum
from playwright.async_api import BrowserContext, Page, Locator, ElementHandle
from core.security import encrypt_credential, decrypt_credential
from automation.browser import launch_browser
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
    
    # Final fallback: query_selector_all with enhanced checks
    inputs = await page.query_selector_all(f"input[type='{input_type}']")
    
    for inp in inputs:
        # Check if element is physically visible (has positive dimensions)
        box = await inp.bounding_box()
        if box and box['width'] > 0 and box['height'] > 0:
            # Check if element is not hidden via CSS
            is_visible = await inp.is_visible()
            # Check if element is enabled
            is_enabled = await inp.is_enabled()
            if is_visible and is_enabled:
                # Return as a Locator instead of building a CSS selector
                return page.locator(f"input[type='{input_type}']").filter(has=inp)
    
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
    
    # Final fallback: query_selector_all with enhanced checks
    buttons = await page.query_selector_all("button")
    matching_buttons = []
    
    for btn in buttons:
        # Check if element is physically visible (has positive dimensions)
        box = await btn.bounding_box()
        if box and box['width'] > 0 and box['height'] > 0:
            # Check if element is not hidden via CSS
            is_visible = await btn.is_visible()
            # Check if element is enabled
            is_enabled = await btn.is_enabled()
            if is_visible and is_enabled:
                # Check if button contains the target text
                text_content = await btn.text_content()
                if text_content and button_text.lower() in text_content.lower():
                    matching_buttons.append(btn)
    
    if not matching_buttons:
        raise ValueError(f"No visible, enabled button containing '{button_text}' found on page")
    
    # Use the last matching button (to target the main sign-in button, not social login)
    return page.locator("button").filter(has=matching_buttons[-1])


async def linkedin_login(email: str, password: str, account: any, keep_alive: bool = False, user_agent: str = None) -> tuple[LinkedInSessionStatus, any]:
    """
    Performs LinkedIn login and returns the session status.
    
    Args:
        email: LinkedIn email
        password: LinkedIn password
        account: LinkedInAccount object (for saving cookies)
        keep_alive: If True, keeps browser session alive for verification (returns session resources)
        user_agent: Specific User-Agent to use (for consistency)
    
    Returns:
        tuple: (LinkedInSessionStatus, session_resources or None)
            - session_resources is only returned when keep_alive=True and status is VERIFICATION_REQUIRED
            - session_resources contains: (pw, browser, context, page, user_agent)
    
    Returns LinkedInSessionStatus: VALID, EXPIRED, CHECKPOINT, VERIFICATION_REQUIRED, or UNKNOWN.
    """
    pw, browser, context, page, actual_user_agent = await launch_browser(user_agent=user_agent)

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
        # TODO: Adjust timeout for deployment - currently increased for slow development internet
        await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=80000)
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
                    if await checkbox.is_visible(timeout=1000):
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
        await random_idle_pause(4, 7)  # Wait for redirect + page load
    
        # ── Step 5: Verify login result and determine status ───────────────────
        logger.info(f"📍 Current URL after login attempt: {page.url}")
        
        # Check for checkpoint/bot detection
        if "/checkpoint" in page.url or "/checkpoint/challenge" in page.url:
            logger.warning("⚠️ LinkedIn security checkpoint detected - possible bot detection")
            if should_take_screenshots():
                await page.screenshot(path="checkpoint_detected.png", full_page=True)
            if keep_alive:
                # Keep session alive for verification
                return (LinkedInSessionStatus.VERIFICATION_REQUIRED, (pw, browser, context, page, user_agent))
            return (LinkedInSessionStatus.CHECKPOINT, None)
        
        # Check for verification required
        if "/verify" in page.url or "verification" in page.url.lower():
            logger.warning("⚠️ LinkedIn requires account verification")
            if should_take_screenshots():
                await page.screenshot(path="verification_required.png", full_page=True)
            if keep_alive:
                # Keep session alive for verification
                return (LinkedInSessionStatus.VERIFICATION_REQUIRED, (pw, browser, context, page, user_agent))
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
            # Save cookies only on successful login and if account is provided
            if account is not None:
                logger.info("💾 Saving active session cookies...")
                await save_session_cookies(page.context, account)
            return (LinkedInSessionStatus.VALID, (pw, browser, context, page, user_agent))
        
        # Unknown state
        logger.warning("⚠️ Unknown page state after login")
        if should_take_screenshots():
            await page.screenshot(path="unknown_state.png", full_page=True)
        return (LinkedInSessionStatus.UNKNOWN, None)
    
    except Exception as e:
        if should_take_screenshots():
            await page.screenshot(path=f"error_screenshot_{random.randint(1000, 9999)}.png", full_page=True)
        logger.error(f"❌ Automation Error Encountered: {str(e)}")
        return (LinkedInSessionStatus.EXPIRED, None)
    finally:
        # Only close browser if not keeping alive for verification
        if not keep_alive:
            logger.info("🧹 Closing browser instance contexts...")
            await context.close()
            await browser.close()
            await pw.stop()


async def save_session_cookies(context: BrowserContext, account, user_agent: str = None) -> None:
    """
    Extracts all linkedin.com cookies from the browser context,
    encrypts them, and saves to the LinkedInAccount row.
    Call this immediately after a successful Playwright login.
    
    account: SQLAlchemy LinkedInAccount object (sync session, from Celery).
    user_agent: User-Agent string used during login (for session consistency).
    """
    logger.info("🍪 Extracting cookies from browser context...")
    all_cookies = await context.cookies()
    logger.debug(f"Total cookies in context: {len(all_cookies)}")
    
    linkedin_cookies = [
        c for c in all_cookies
        if ".linkedin.com" in c.get("domain", "") or "linkedin.com" in c.get("domain", "")
    ]
    logger.debug(f"LinkedIn cookies after filtering: {len(linkedin_cookies)}")
    
    # Log critical cookies for debugging (development only)
    if should_log_debug():
        li_at_cookie = next((c for c in linkedin_cookies if c.get("name") == "li_at"), None)
        if li_at_cookie:
            logger.debug(f"li_at cookie found - Domain: {li_at_cookie.get('domain')}, Expires: {li_at_cookie.get('expires')}")
        else:
            logger.warning("⚠️ li_at cookie NOT found in LinkedIn cookies!")
        
        # Log all LinkedIn cookie names
        cookie_names = [c.get("name") for c in linkedin_cookies]
        logger.debug(f"LinkedIn cookie names: {cookie_names}")
 
    if not linkedin_cookies:
        raise ValueError("No LinkedIn cookies found after login — session may have failed")
 
    cookie_json = json.dumps(linkedin_cookies)
    logger.debug(f"Encrypting {len(cookie_json)} bytes of cookie JSON...")
    encrypted = encrypt_credential(cookie_json)
    logger.debug(f"Encrypted cookie blob length: {len(encrypted)} bytes")
 
    # Update the DB row (sync SQLAlchemy session in Celery context)
    account.encrypted_cookies = encrypted
    account.cookies_updated_at = datetime.now(timezone.utc)
    if user_agent:
        account.user_agent = user_agent
        logger.debug(f"User-Agent saved to account: {user_agent}")
    logger.info(f"💾 Cookies saved to account. Updated at: {account.cookies_updated_at}")
 
 
async def load_session_cookies(context: BrowserContext, account) -> bool:
    """
    Loads saved cookies into the browser context.
    Returns True if cookies were loaded, False if none saved.
    """
    logger.info("🔓 Checking for encrypted cookies in account...")
    logger.debug(f"📅 Cookies last updated at: {account.cookies_updated_at}")
    logger.debug(f"🔍 Saved User-Agent: {account.user_agent}")
    
    if not account.encrypted_cookies:
        logger.warning("⚠️ No encrypted cookies found in account")
        return False

    logger.debug(f"Decrypting cookie blob (length: {len(account.encrypted_cookies)} bytes)...")
    cookie_json = decrypt_credential(account.encrypted_cookies)
    logger.debug(f"Decrypted JSON length: {len(cookie_json)} bytes")
    
    cookies = json.loads(cookie_json)
    logger.debug(f"Loaded {len(cookies)} cookies from database")
    
    # Check for critical li_at cookie
    li_at = None
    for cookie in cookies:
        if cookie.get('name') == 'li_at':
            li_at = cookie
            break
    
    if li_at:
        logger.debug(f"li_at cookie found - Domain: {li_at.get('domain')}, Expires: {li_at.get('expires')}")
        # Check if expired
        if li_at.get('expires'):
            from datetime import datetime, timezone
            expires_ts = li_at.get('expires')
            if isinstance(expires_ts, (int, float)):
                expires_dt = datetime.fromtimestamp(expires_ts, tz=timezone.utc)
                now = datetime.now(timezone.utc)
                if expires_dt < now:
                    logger.warning(f"⚠️ li_at cookie EXPIRED! Expired at: {expires_dt}")
                else:
                    logger.debug(f"li_at cookie still valid. Expires at: {expires_dt}")
    else:
        logger.warning("⚠️ li_at cookie NOT found in saved cookies!")
    
    # Log all cookie names for debugging (development only)
    if should_log_debug():
        cookie_names = [c.get("name") for c in cookies]
        logger.debug(f"Loaded cookie names: {cookie_names}")

    # Playwright requires 'sameSite' to be a specific string
    for c in cookies:
        if c.get("sameSite") not in ("Strict", "Lax", "None"):
            c["sameSite"] = "Lax"

    logger.info("➕ Adding cookies to browser context...")
    await context.add_cookies(cookies)
    logger.info("✅ Cookies successfully added to browser context")
    
    # Verify cookies were actually added
    final_cookies = await context.cookies()
    linkedin_final = [c for c in final_cookies if "linkedin.com" in c.get("domain", "")]
    logger.debug(f"Final LinkedIn cookies in context: {len(linkedin_final)}")
    
    # Check which cookie was lost (development only)
    if should_log_debug():
        final_cookie_names = [c.get("name") for c in linkedin_final]
        lost_cookies = set(cookie_names) - set(final_cookie_names)
        if lost_cookies:
            logger.warning(f"⚠️ Cookies lost during add: {lost_cookies}")
            # If __cf_bm is lost, try to add it manually with different settings
            if "__cf_bm" in lost_cookies:
                logger.info("🔧 Attempting to manually add __cf_bm cookie...")
                for cookie in cookies:
                    if cookie.get("name") == "__cf_bm":
                        # Try adding with modified settings
                        manual_cookie = cookie.copy()
                        manual_cookie["sameSite"] = "None"
                        manual_cookie["secure"] = True
                        try:
                            await context.add_cookies([manual_cookie])
                            logger.info("✅ Manually added __cf_bm cookie")
                        except Exception as e:
                            logger.warning(f"⚠️ Could not manually add __cf_bm: {str(e)}")
                        break
    
    return True
 
 
async def verify_session(page: Page) -> SessionVerificationResult:
    """
    Navigates to LinkedIn feed to check if the loaded session is still valid.
    Returns SessionVerificationResult with detailed status.
    """
    logger.info("🔍 Navigating to LinkedIn feed to verify session...")
    # TODO: Decrease timeout for production (currently 120s for local network development)
    await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=120000)
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
        await page.wait_for_selector("[data-control-name='nav.home']", timeout=5000)
        logger.info("✅ Session valid - found home navigation element")
        return SessionVerificationResult(
            status=LinkedInSessionStatus.VALID,
            url=page.url,
            message="Session is valid and active"
        )
    except Exception:
        # Try alternate feed indicator
        try:
            await page.wait_for_selector(".feed-identity-module", timeout=5000)
            logger.info("✅ Session valid - found feed identity module")
            return SessionVerificationResult(
                status=LinkedInSessionStatus.VALID,
                url=page.url,
                message="Session is valid and active"
            )
        except Exception:
            # Try checking for any LinkedIn navigation element
            try:
                await page.wait_for_selector(".global-nav", timeout=5000)
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


             

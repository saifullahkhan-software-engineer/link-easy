"""
LinkedIn Account endpoints.
All routes require a valid Bearer access token (authenticated platform user).
Users can only read/modify their own LinkedIn accounts.
Admins can read any account (but still cannot see plaintext passwords — no one can).
POST   /api/v1/linkedin/account          — add a LinkedIn account
GET    /api/v1/linkedin/account          — get the user's account
PATCH  /api/v1/linkedin/account          — update label / password
DELETE /api/v1/linkedin/account          — remove account
"""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_current_user, get_db, require_roles
from core.security import decrypt_credential, encrypt_credential
from models.linkedin_account import LinkedInAccount, LinkedInAccountStatus 
from models.roles import UserRole
from models.user import User
from schemas.linkedin import (
    LinkedInAccountCreate,
    LinkedInAccountDeleteResponse,
    LinkedInAccountResponse,
    LinkedInAccountUpdate,
    LinkedInAccountCreateResponse,
    VerificationCodeRequest,
    VerificationCodeResponse,
    SessionVerificationResponse,
)
# import linkdin login from session 
from automation.session  import linkedin_login, LinkedInSessionStatus, load_session_cookies, verify_session, save_session_cookies
from automation.session_manager import session_manager
from automation.browser import launch_browser
from core import email


# add logger 
import logging
from core.logging_config import get_logger, should_log_debug, should_take_screenshots

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/linkedin", tags=["linkedin-accounts"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_account_or_404(
    owner_email: str,
    db: AsyncSession,
) -> LinkedInAccount:
    """
    Fetch a LinkedIn account by owner email.
    Raises 404 if not found.
    """
    result = await db.execute(
        select(LinkedInAccount).where(LinkedInAccount.owner_email == owner_email)
    )
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/account",
    response_model=LinkedInAccountCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a LinkedIn account",
)
async def add_linkedin_account(
    payload: LinkedInAccountCreate,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),
) -> LinkedInAccountCreateResponse:
    """
    Accepts the user's LinkedIn email and password, encrypts the password
    with AES-256-GCM, and attempts LinkedIn login.

    Returns two possible outcomes:
    1. LOGIN_SUCCESS: Login completed, cookies saved, account created with ACTIVE status
    2. PENDING_VERIFICATION: LinkedIn requires verification code, session kept alive
    
    The plaintext password is never logged or stored.
    """
    
    encrypted = encrypt_credential(payload.linkedin_password)

    # Attempt login with keep_alive=True to support verification flow
    session_status, session_resources = await linkedin_login(
        email=payload.linkedin_email, 
        password=payload.linkedin_password, 
        account=None,  # Don't pass account yet, will create after verification
        keep_alive=True
    )
    
    # Scenario 1: Login successful - create account with ACTIVE status
    if session_status == LinkedInSessionStatus.VALID:
        # Create a temporary account object to save cookies
        from models.linkedin_account import LinkedInAccount
        temp_account = LinkedInAccount(
            owner_email=payload.owner_email,
            linkedin_email=str(payload.linkedin_email).lower().strip(),
            encrypted_password=encrypted,
            label=payload.label,
            status=LinkedInAccountStatus.ACTIVE,
        )
        
        # Save cookies to temp account
        pw, browser, context, page, user_agent = session_resources
        from automation.session import save_session_cookies
        await save_session_cookies(context, temp_account, user_agent)
        
        # Clean up browser resources
        await context.close()
        await browser.close()
        await pw.stop()
        
        # Persist account to database
        db.add(temp_account)
        try:   
            await db.commit()
            await db.refresh(temp_account)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A LinkedIn account is already connected to your profile",
            )
        
        return LinkedInAccountCreateResponse(
            status="LOGIN_SUCCESS",
            session_id=None,
            message="LinkedIn account added successfully",
            account=LinkedInAccountResponse.model_validate(temp_account)
        )
    
    # Scenario 2: Verification required - keep session alive
    elif session_status == LinkedInSessionStatus.VERIFICATION_REQUIRED:
        pw, browser, context, page, user_agent = session_resources
        
        # Create pending session in session manager
        session_id = session_manager.create_session(
            linkedin_email=str(payload.linkedin_email).lower().strip(),
            owner_email=payload.owner_email,
            label=payload.label,
            pw=pw,
            browser=browser,
            context=context,
            page=page,
            encrypted_password=encrypted,
            user_agent=user_agent
        )
        
        return LinkedInAccountCreateResponse(
            status="PENDING_VERIFICATION",
            session_id=session_id,
            message="LinkedIn requires verification code. Please use the verification endpoint.",
            account=None
        )
    
    # Scenario 3: Login failed - return error
    else:
        # Clean up browser resources
        if session_resources:
            pw, browser, context, page, user_agent = session_resources
            await context.close()
            await browser.close()
            await pw.stop()
        
        error_message = "Login failed"
        if session_status == LinkedInSessionStatus.CHECKPOINT:
            error_message = "LinkedIn security checkpoint detected - possible bot detection"
        elif session_status == LinkedInSessionStatus.EXPIRED:
            error_message = "Invalid credentials or login failed"
        elif session_status == LinkedInSessionStatus.UNKNOWN:
            error_message = "Unknown error during login"
        
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_message
        )


@router.get(
    "/account",
    response_model=LinkedInAccountResponse,
    summary="Get your LinkedIn account",
)
async def get_linkedin_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LinkedInAccountResponse:
    """Returns the LinkedIn account for the authenticated user."""
    account = await _get_account_or_404(current_user.email, db)
    return LinkedInAccountResponse.model_validate(account)


@router.patch(
    "/account",
    response_model=LinkedInAccountResponse,
    summary="Update a LinkedIn account",
)
async def update_linkedin_account(
    payload: LinkedInAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LinkedInAccountResponse:
    """
    Update the label or password of a LinkedIn account.
    If a new password is provided it is re-encrypted before storage.
    On password update the status is reset to pending_verification so
    Playwright can re-confirm the credentials.
    """
    account = await _get_account_or_404(current_user.email, db)

    if payload.label is not None:
        account.label = payload.label

    if payload.linkedin_password is not None:
        account.encrypted_password = encrypt_credential(payload.linkedin_password)
        # Reset status — credentials changed, need re-verification
        account.status = LinkedInAccountStatus.PENDING_VERIFICATION
    
    if payload.linkedin_email is not None:
        account.linkedin_email = str(payload.linkedin_email).lower().strip()

    await db.commit()
    await db.refresh(account)
    return LinkedInAccountResponse.model_validate(account)


@router.delete(
    "/account",
    response_model=LinkedInAccountDeleteResponse,
    summary="Remove a LinkedIn account",
)
async def delete_linkedin_account(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> LinkedInAccountDeleteResponse:
    """Permanently removes a LinkedIn account. This action cannot be undone."""
    account = await _get_account_or_404(current_user.email, db)
    await db.delete(account)
    await db.commit()
    return LinkedInAccountDeleteResponse(message="LinkedIn account removed successfully")


# ---------------------------------------------------------------------------
# Verification code submission endpoint
# ---------------------------------------------------------------------------

@router.post(
    "/account/verify",
    response_model=VerificationCodeResponse,
    summary="Submit verification code for pending LinkedIn login",
)
async def submit_verification_code(
    payload: VerificationCodeRequest,
    db: AsyncSession = Depends(get_db),
) -> VerificationCodeResponse:
    """
    Submits a verification code for a pending LinkedIn login session.
    
    This endpoint retrieves the existing Playwright session (does not create a new one),
    navigates to the verification page, enters the code, and completes the login flow.
    """
    # Retrieve pending session
    pending_session = session_manager.get_session(payload.session_id)
    if not pending_session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found or expired. Please start a new login."
        )
    
    page = pending_session.page
    context = pending_session.context
    browser = pending_session.browser
    pw = pending_session.pw
    
    try:
        # Find verification code input field
        from automation.session import find_visible_input_by_type
        from automation.human import random_idle_pause, find_and_click_resilient
        from automation.session import save_session_cookies
        
        logger.info("🔢 Looking for verification code input field...")
        
        # Try to find verification input
        code_input = None
        try:
            code_input = await find_visible_input_by_type(page, "text")
        except:
            pass
        
        if not code_input:
            # Try with type="tel" (common for verification codes)
            try:
                code_input = page.locator("input[type='tel']").first()
                if await code_input.is_visible():
                    logger.debug("Found verification input (tel type)")
                else:
                    code_input = None
            except:
                code_input = None
        
        if not code_input:
            # Try generic input selectors
            inputs = await page.query_selector_all("input")
            for inp in inputs:
                inp_type = await inp.get_attribute("type")
                inp_placeholder = await inp.get_attribute("placeholder")
                if inp_type in ["text", "tel"] or (inp_placeholder and "code" in inp_placeholder.lower()):
                    if await inp.is_visible():
                        code_input = inp
                        break
        
        if not code_input:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not find verification code input field. Session may have expired."
            )
        
        # Enter verification code
        logger.info("✍️ Entering verification code...")
        await code_input.fill(payload.verification_code)
        await random_idle_pause(0.5, 1.0)
        
        # Find and click submit button
        logger.info("🚀 Looking for submit button...")
        submit_selectors = [
            "button[type='submit']",
            "button:has-text('Verify')",
            "button:has-text('Submit')",
            "button:has-text('Continue')",
            "button:has-text('Confirm')",
        ]
        
        try:
            await find_and_click_resilient(page, submit_selectors, "Submit Button")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not find submit button: {str(e)}"
            )
        
        # Wait for redirect and check result
        await random_idle_pause(3, 5)
        logger.debug(f"Current URL after verification: {page.url}")
        
        # Check if verification succeeded (redirected to feed)
        if "/feed" in page.url:
            logger.info("✅ Verification successful - on feed page")
            
            # Create account object
            account = LinkedInAccount(
                owner_email=pending_session.owner_email,
                linkedin_email=pending_session.linkedin_email,
                encrypted_password=pending_session.encrypted_password,
                label=pending_session.label,
                status=LinkedInAccountStatus.ACTIVE,
            )
            
            # Save cookies with user_agent
            await save_session_cookies(context, account, pending_session.user_agent)
            
            # Persist to database
            db.add(account)
            try:
                await db.commit()
                await db.refresh(account)
            except IntegrityError:
                await db.rollback()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="A LinkedIn account is already connected to your profile",
                )
            
            # Clean up session
            await session_manager.cleanup_session(payload.session_id)
            
            return VerificationCodeResponse(
                status="LOGIN_SUCCESS",
                message="Verification successful. LinkedIn account added.",
                account=LinkedInAccountResponse.model_validate(account)
            )
        
        # Check if verification failed (still on verification page or error)
        elif "/verify" in page.url or "/checkpoint" in page.url or "/login" in page.url:
            logger.warning("⚠️ Verification failed - still on verification/error page")
            # Keep session alive for retry
            return VerificationCodeResponse(
                status="VERIFICATION_FAILED",
                message="Verification code invalid or expired. Please try again.",
                account=None
            )
        
        else:
            logger.warning("⚠️ Unknown state after verification")
            # Keep session alive for debugging
            return VerificationCodeResponse(
                status="VERIFICATION_FAILED",
                message=f"Unexpected state after verification. Current URL: {page.url}",
                account=None
            )
            
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"❌ Error during verification: {str(e)}")
        # Clean up session on error
        await session_manager.cleanup_session(payload.session_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error during verification: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Admin-only route — update account status (e.g. suspend an account)
# ---------------------------------------------------------------------------

@router.patch(
    "/admin/accounts/{linkedin_email}/status",
    response_model=LinkedInAccountResponse,
    summary="[Admin] Update account status",
)
async def admin_update_account_status(
    linkedin_email: str,
    new_status: LinkedInAccountStatus,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
) -> LinkedInAccountResponse:
    """
    Admin endpoint to change the status of any LinkedIn account.
    Useful for suspending accounts that trigger LinkedIn security checks.
    """
    result = await db.execute(
        select(LinkedInAccount).where(LinkedInAccount.linkedin_email == linkedin_email)
    )
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    account.status = new_status
    await db.commit()
    await db.refresh(account)
    return LinkedInAccountResponse.model_validate(account)


# ---------------------------------------------------------------------------
# Session Verification Endpoint — validates and refreshes LinkedIn sessions
# ---------------------------------------------------------------------------

@router.post(
    "/account/verify-session",
    response_model=SessionVerificationResponse,
    summary="Verify and refresh LinkedIn session",
)
async def verify_linkedin_session(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SessionVerificationResponse:
    """
    Verifies if the user's LinkedIn session is still active.
    
    - If session is valid: Returns ACTIVE status
    - If session is expired: Attempts automatic relogin
    - If relogin succeeds: Returns REFRESHED status with new cookies
    - If relogin requires verification: Returns PENDING_VERIFICATION status and sends email notification
    - If relogin fails: Returns FAILED status
    
    This endpoint integrates with existing authentication, Playwright automation,
    cookie management, and email notification systems.
    """
    logger.info("🔍 Starting LinkedIn session verification for user: %s", current_user.email)
    
    # Get the user's LinkedIn account
    result = await db.execute(
        select(LinkedInAccount).where(
            LinkedInAccount.owner_email == current_user.email
        )
    )
    account = result.scalars().first()
    
    if not account:
        logger.warning("⚠️ No LinkedIn account found for user: %s", current_user.email)
        return SessionVerificationResponse(
            status="FAILED",
            message="No LinkedIn account found. Please add your LinkedIn account first.",
            account=None,
            requires_manual_verification=False
        )
    
    logger.info("✅ Found LinkedIn account: %s (status: %s)", account.linkedin_email, account.status)
    
    # Check if account is already in pending verification state
    if account.status == LinkedInAccountStatus.PENDING_VERIFICATION:
        logger.warning("⚠️ Account already in PENDING_VERIFICATION state")
        return SessionVerificationResponse(
            status="PENDING_VERIFICATION",
            message="Account is already pending manual verification. Please complete the verification process.",
            account=LinkedInAccountResponse.model_validate(account),
            requires_manual_verification=True
        )
    
    pw = None
    browser = None
    context = None
    page = None
    
    try:
        # Step 1: Launch browser with saved User-Agent if available
        logger.info("🚀 Launching browser for session verification...")
        user_agent = account.user_agent
        pw, browser, context, page, actual_user_agent = await launch_browser(user_agent=user_agent)
        logger.debug(f"Browser launched with User-Agent: {actual_user_agent}")
        
        # Step 2: Load existing cookies
        logger.info("🍪 Loading stored session cookies...")
        cookies_loaded = await load_session_cookies(context, account)
        
        if not cookies_loaded:
            logger.warning("⚠️ No stored cookies found, proceeding to fresh login...")
            verification_result = None
        else:
            logger.info("✅ Cookies loaded successfully")
            
            # Step 3: Verify session by navigating to feed
            logger.info("🔐 Verifying session validity...")
            verification_result = await verify_session(page)
            logger.debug(f"Verification result: {verification_result.status.value} - {verification_result.message}")
        
        # Step 4: Handle verification result
        if verification_result and verification_result.status == LinkedInSessionStatus.VALID:
            # Session is valid
            logger.info("✅ LinkedIn session is ACTIVE")
            await db.refresh(account)
            return SessionVerificationResponse(
                status="ACTIVE",
                message="Your LinkedIn session is active and working.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )
        
        # Step 5: Session expired or no cookies - attempt automatic relogin
        logger.info("🔄 Session expired or invalid, attempting automatic relogin...")
        logger.info("🔐 Decrypting credentials for relogin...")
        
        # Close the first browser before launching a second one to prevent resource leak
        if context:
            await context.close()
        if browser:
            await browser.close()
        if pw:
            await pw.stop()
        
        from core.security import decrypt_credential
        password = decrypt_credential(account.encrypted_password)
        
        logger.info("🚀 Initiating fresh login with stored credentials...")
        session_status, session_resources = await linkedin_login(
            email=account.linkedin_email,
            password=password,
            account=None,
            keep_alive=False
        )
        
        pw, browser, context, page, user_agent = session_resources
        
        # Step 6: Handle login result
        if session_status == LinkedInSessionStatus.VALID:
            # Login successful - save new cookies
            logger.info("✅ Automatic relogin successful")
            await save_session_cookies(context, account, user_agent)
            
            # Update account status to ACTIVE
            account.status = LinkedInAccountStatus.ACTIVE
            await db.commit()
            await db.refresh(account)
            
            logger.info("✅ Session refreshed successfully")
            return SessionVerificationResponse(
                status="REFRESHED",
                message="Your LinkedIn session expired but was successfully refreshed with new cookies.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )
        
        elif session_status == LinkedInSessionStatus.VERIFICATION_REQUIRED:
            # Verification required - manual intervention needed
            logger.warning("⚠️ LinkedIn requires verification for relogin")
            
            # Update account status
            account.status = LinkedInAccountStatus.PENDING_VERIFICATION
            await db.commit()
            await db.refresh(account)
            
            # Send email notification
            try:
                await email.send_verification_email(
                    email=current_user.email,
                    code="MANUAL_VERIFICATION_REQUIRED"
                )
                logger.info("✅ Email notification sent to user: %s", current_user.email)
            except Exception as email_error:
                logger.error(f"❌ Failed to send email notification: {str(email_error)}")
            
            return SessionVerificationResponse(
                status="PENDING_VERIFICATION",
                message="Your LinkedIn session expired and requires manual verification. An email has been sent with instructions.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=True
            )
        
        else:
            # Login failed - checkpoint, expired, or unknown
            logger.warning("⚠️ Automatic relogin failed with status: %s", session_status.value)
            
            # Update account status to indicate failure
            account.status = LinkedInAccountStatus.FAILED
            await db.commit()
            await db.refresh(account)
            
            error_message = "Automatic relogin failed"
            if session_status == LinkedInSessionStatus.CHECKPOINT:
                error_message = "LinkedIn security checkpoint detected during relogin"
            elif session_status == LinkedInSessionStatus.EXPIRED:
                error_message = "Invalid credentials during relogin"
            
            return SessionVerificationResponse(
                status="FAILED",
                message=error_message,
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )
    
    except HTTPException:
        # Re-raise HTTP exceptions
        raise
    except Exception as e:
        logger.error(f"❌ Error during session verification: {str(e)}")
        return SessionVerificationResponse(
            status="FAILED",
            message=f"An error occurred during session verification: {str(e)}",
            account=None,
            requires_manual_verification=False
        )
    
    finally:
        # Clean up Playwright resources
        if page:
            try:
                await page.close()
            except:
                pass
        if context:
            try:
                await context.close()
            except:
                pass
        if browser:
            try:
                await browser.close()
            except:
                pass
        if pw:
            try:
                await pw.stop()
            except:
                pass
        logger.debug("🧹 Playwright resources cleaned up")

"""
LinkedIn Account endpoints.
All routes require a valid Bearer access token (authenticated platform user).
Users can only read/modify their own LinkedIn accounts.
Admins can read any account (but still cannot see plaintext passwords — no one can).
POST   /api/v1/linkedin/account          — add a LinkedIn account
GET    /api/v1/linkedin/account          — get the user's account
PATCH  /api/v1/linkedin/account          — update label / password
DELETE /api/v1/linkedin/account          — remove account

Session state lives in each account's durable Chromium profile directory
(account.profile_dir) — NOT in the database. Adding an account creates the
row + profile directory first, then logs in inside that persistent profile;
LinkedIn's session cookies are persisted to disk by Chromium automatically.
Credential-based relogin is a FALLBACK used only when the persistent
profile's session has expired.
"""

import shutil

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
from automation.session import (
    linkedin_login,
    LinkedInSessionStatus,
    verify_session,
    uncheck_all_checkboxes,
)
from automation.session_manager import session_manager
from automation.browser import launch_persistent_browser, ensure_profile_dir
from worker.profile_lock import (
    ProfileInUseError,
    acquire_profile_lock,
    release_profile_lock,
)


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
    with AES-256-GCM, creates the account's durable browser profile, and
    attempts LinkedIn login inside that persistent profile.

    Returns two possible outcomes:
    1. LOGIN_SUCCESS: Login completed inside the persistent profile (session
       persisted to disk by Chromium), account ACTIVE.
    2. PENDING_VERIFICATION: LinkedIn requires a verification code; the
       browser session is kept alive for the verification endpoint.

    The plaintext password is never logged or stored.
    """

    encrypted = encrypt_credential(payload.linkedin_password)
    linkedin_email = str(payload.linkedin_email).lower().strip()

    # Fail fast on duplicates BEFORE launching any browser.
    existing = await db.execute(
        select(LinkedInAccount).where(
            (LinkedInAccount.linkedin_email == linkedin_email)
            | (LinkedInAccount.owner_email == payload.owner_email)
        )
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A LinkedIn account is already connected to your profile",
        )

    # Create the account row up front so the login can run inside its durable
    # profile directory. profile_dir is derived ONLY from the server-generated
    # UUID — never from the (user-supplied) email address.
    account = LinkedInAccount(
        owner_email=payload.owner_email,
        linkedin_email=linkedin_email,
        encrypted_password=encrypted,
        label=payload.label,
        status=LinkedInAccountStatus.PENDING_VERIFICATION,
    )
    account.assign_profile_dir()   # sets id + profile_dir from the UUID
    ensure_profile_dir(account)    # mkdir with 0o700 permissions

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

    lock = None
    try:
        # Per-account profile lock — fail fast if something else holds it.
        lock = acquire_profile_lock(account.id, blocking_timeout=0)

        # Attempt login inside the persistent profile (keep_alive=True to
        # support the verification-code flow).
        session_status, session_resources, login_error_detail = await linkedin_login(
            email=account.linkedin_email,
            password=payload.linkedin_password,
            account=account,
            keep_alive=True,
        )
        # Persist the fingerprint pinned at first launch (no-op afterwards).
        await db.commit()

        # Scenario 1: Login successful — session now lives in the profile dir.
        if session_status == LinkedInSessionStatus.VALID:
            pw, browser, context, page, user_agent = session_resources

            # Clean up browser resources (the profile dir keeps the session)
            await context.close()
            await pw.stop()

            account.status = LinkedInAccountStatus.ACTIVE
            await db.commit()
            await db.refresh(account)

            return LinkedInAccountCreateResponse(
                status="LOGIN_SUCCESS",
                session_id=None,
                message="LinkedIn account added successfully",
                account=LinkedInAccountResponse.model_validate(account)
            )

        # Scenario 2: Verification required — keep session alive.
        elif session_status == LinkedInSessionStatus.VERIFICATION_REQUIRED:
            pw, browser, context, page, user_agent = session_resources

            # Create pending session in session manager. The profile lock
            # transfers to the pending session because the browser context
            # (and its grip on the profile dir) stays open until the code
            # is submitted or the session expires.
            session_id = session_manager.create_session(
                linkedin_email=account.linkedin_email,
                owner_email=payload.owner_email,
                label=payload.label,
                pw=pw,
                browser=browser,
                context=context,
                page=page,
                encrypted_password=encrypted,
                user_agent=user_agent,
                profile_lock=lock,
            )
            lock = None  # ownership transferred to the pending session

            return LinkedInAccountCreateResponse(
                status="PENDING_VERIFICATION",
                session_id=session_id,
                message="LinkedIn requires verification code. Please use the verification endpoint.",
                account=None
            )

        # Scenario 3: Login failed — remove the account row + profile dir so
        # the user can retry cleanly.
        else:
            # Clean up browser resources (if any were returned)
            if session_resources:
                pw, browser, context, page, user_agent = session_resources
                await context.close()
                await pw.stop()

            profile_dir = account.profile_dir
            await db.delete(account)
            await db.commit()
            shutil.rmtree(profile_dir, ignore_errors=True)

            error_message = "Login failed"
            if login_error_detail:
                # LinkedIn's own on-page rejection text (or captcha note),
                # scraped by the login flow — far more actionable than the
                # generic messages below.
                error_message = login_error_detail
            elif session_status == LinkedInSessionStatus.CHECKPOINT:
                error_message = "LinkedIn security checkpoint detected - possible bot detection"
            elif session_status == LinkedInSessionStatus.EXPIRED:
                error_message = "Invalid credentials or login failed"
            elif session_status == LinkedInSessionStatus.UNKNOWN:
                error_message = "Unknown error during login"

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_message
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error while adding LinkedIn account: {str(e)}")
        # Best-effort: remove the partially-created account so the user can retry.
        try:
            await db.delete(account)
            await db.commit()
        except Exception:
            await db.rollback()
        shutil.rmtree(account.profile_dir, ignore_errors=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not complete LinkedIn login: {str(e)}"
        )
    finally:
        # No-op if the lock was transferred to a pending session (lock=None)
        # or never acquired.
        release_profile_lock(lock)


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
    profile_dir = account.profile_dir
    await db.delete(account)
    await db.commit()
    # Remove the durable profile directory too — the session data on disk
    # belongs to this account and must not outlive it.
    shutil.rmtree(profile_dir, ignore_errors=True)
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
    The account row already exists (created when the login was started); on
    success it is simply flipped to ACTIVE — the verified session persists in
    the account's durable profile directory.
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

        # Uncheck any checkboxes (e.g., "Remember this device") before submitting.
        # Shared Locator-based helper — the old inline copy crashed on
        # ElementHandle.is_visible(timeout=...) (ElementHandle accepts no
        # timeout) and duplicated every box found by multiple selectors.
        logger.info("🔲 Unchecking any checkboxes before verification...")
        await uncheck_all_checkboxes(page, context_label="verification")

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
        if should_log_debug():
            logger.debug(f"Current URL after verification: {page.url}")

        # Check if verification succeeded (redirected to feed)
        if "/feed" in page.url:
            logger.info("✅ Verification successful - on feed page")

            # The account row was created when the login was started — flip
            # it to ACTIVE. The verified session now lives in the account's
            # durable profile directory; nothing to save to the database.
            result = await db.execute(
                select(LinkedInAccount).where(
                    LinkedInAccount.linkedin_email == pending_session.linkedin_email
                )
            )
            account = result.scalars().first()
            if account is None:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Account record missing for pending session. Please re-add the account.",
                )

            account.status = LinkedInAccountStatus.ACTIVE
            await db.commit()
            await db.refresh(account)

            # Clean up session (closes browser + releases the profile lock)
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
    new_status: str,  # Accept string and convert to enum
    db: AsyncSession = Depends(get_db),
    # TEMPORARY: Commented out auth for testing
    # current_user: User = Depends(require_roles([UserRole.ADMIN.value])),
) -> LinkedInAccountResponse:
    """
    Admin endpoint to change the status of any LinkedIn account.
    Useful for suspending accounts that trigger LinkedIn security checks.

    TEMPORARY: Auth disabled for testing.
    """
    # Convert string to enum (case-insensitive)
    try:
        status_enum = LinkedInAccountStatus(new_status.lower())
        logger.info(f"Converting status '{new_status}' to enum: {status_enum.value}")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status. Valid values: {[s.value for s in LinkedInAccountStatus]}"
        )

    result = await db.execute(
        select(LinkedInAccount).where(LinkedInAccount.linkedin_email == linkedin_email)
    )
    account = result.scalars().first()
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    # Update status on the ORM object. With the corrected model,
    # SQLAlchemy will handle the enum type correctly.
    account.status = status_enum
    await db.commit()

    # Refresh to get updated data (e.g., updated_at from DB trigger/default)
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
    owner_email: str,
    db: AsyncSession = Depends(get_db),
) -> SessionVerificationResponse:
    """
    Verifies if the user's LinkedIn session is still active by opening the
    account's durable browser profile.

    - If session is valid: Returns ACTIVE status (no save step needed — the
      session persists in the profile directory).
    - If LinkedIn demands a checkpoint/verification code: the live browser is
      handed to the pending-session flow (PENDING_VERIFICATION + email).
    - If session is EXPIRED: falls back to credential-based relogin inside the
      same persistent profile (this is the ONLY path that reads
      encrypted_password).
    - If relogin fails: Returns FAILED status.

    TEMPORARY: Uses owner_email query parameter to bypass auth for testing.
    """
    logger.info("🔍 Starting LinkedIn session verification for user: %s", owner_email)

    # Get the user's LinkedIn account
    result = await db.execute(
        select(LinkedInAccount).where(
            LinkedInAccount.owner_email == owner_email
        )
    )
    account = result.scalars().first()

    if not account:
        logger.warning("⚠️ No LinkedIn account found for user: %s", owner_email)
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

    lock = None
    pw = None
    context = None
    page = None
    pending_session_created = False  # Track if we created a pending session that should stay alive

    try:
        # Step 1: Acquire the per-account profile lock — fail fast with a
        # clear error if a campaign task / another request holds the profile.
        try:
            lock = acquire_profile_lock(account.id, blocking_timeout=0)
        except ProfileInUseError as exc:
            logger.warning("⚠️ Profile busy: %s", exc)
            return SessionVerificationResponse(
                status="IN_USE",
                message="This LinkedIn account is currently in use by another session. Please try again in a few minutes.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )

        # Step 2: Open the persistent profile and verify the session inside it.
        logger.info("🚀 Launching persistent browser for session verification...")
        pw, _, context, page = await launch_persistent_browser(account, headless=True)
        # Persist the fingerprint pinned at first-ever launch (no-op afterwards).
        await db.commit()

        logger.info("🔐 Verifying session validity...")
        verification_result = await verify_session(page)
        logger.info("🔍 Verification result: %s - %s",
                    verification_result.status.value, verification_result.message)

        # Step 3: Valid → done. The session lives on disk in the profile dir.
        if verification_result.status == LinkedInSessionStatus.VALID:
            logger.info("✅ LinkedIn session is ACTIVE in the persistent profile")

            await context.close()
            context = None
            await pw.stop()
            pw = None

            await db.refresh(account)
            return SessionVerificationResponse(
                status="ACTIVE",
                message="Your LinkedIn session is active and working.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )

        # Step 4: Checkpoint / verification code required → hand the live
        # browser to the pending-session flow. No password is used here.
        if verification_result.status in (
            LinkedInSessionStatus.CHECKPOINT,
            LinkedInSessionStatus.VERIFICATION_REQUIRED,
        ):
            logger.warning("⚠️ LinkedIn requires verification — creating pending session")

            session_id = session_manager.create_session(
                linkedin_email=account.linkedin_email,
                owner_email=owner_email,
                label=account.label,
                pw=pw,
                browser=None,
                context=context,
                page=page,
                encrypted_password=account.encrypted_password,
                user_agent=account.user_agent,
                profile_lock=lock,  # lock stays held while the context is open
            )
            lock = None            # ownership transferred to the pending session
            pw = context = page = None  # resources now owned by session_manager
            pending_session_created = True

            account.status = LinkedInAccountStatus.PENDING_VERIFICATION
            await db.commit()
            await db.refresh(account)

            try:
                from core.email import send_verification_email
                await send_verification_email(
                    email=owner_email,
                    code="MANUAL_VERIFICATION_REQUIRED"
                )
                logger.info("✅ Email notification sent to user: %s", owner_email)
            except Exception as email_error:
                logger.error(f"❌ Failed to send email notification: {str(email_error)}")

            return SessionVerificationResponse(
                status="PENDING_VERIFICATION",
                message=f"LinkedIn requires verification. Session ID: {session_id}. Use this to submit the verification code.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=True,
                session_id=session_id
            )

        # Step 5: EXPIRED (or UNKNOWN) → credential-based relogin FALLBACK.
        # This is the ONLY place encrypted_password is decrypted for an
        # already-linked account — normal campaign runs and the valid-session
        # path above never touch it. Relogin happens inside the SAME
        # persistent profile, so the refreshed session is durable.
        logger.info("🔄 Persistent-profile session expired, attempting credential relogin fallback...")

        # Close the verification context first — the same profile dir cannot
        # be open twice. (The profile lock stays held across both launches.)
        await context.close()
        context = None
        await pw.stop()
        pw = None

        password = decrypt_credential(account.encrypted_password)

        session_status, session_resources, login_error_detail = await linkedin_login(
            email=account.linkedin_email,
            password=password,
            account=account,
            keep_alive=True,  # Keep alive for checkpoint/verification
        )
        await db.commit()  # persist any fingerprint changes (no-op after first login)

        if session_status == LinkedInSessionStatus.VALID:
            # Login successful — session persisted in the profile directory.
            logger.info("✅ Credential relogin fallback successful")
            pw, _, context, page, user_agent = session_resources

            await context.close()
            context = None
            await pw.stop()
            pw = None

            account.status = LinkedInAccountStatus.ACTIVE
            await db.commit()
            await db.refresh(account)

            logger.info("✅ Session refreshed successfully")
            return SessionVerificationResponse(
                status="REFRESHED",
                message="Your LinkedIn session expired but was successfully refreshed.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=False
            )

        elif session_status == LinkedInSessionStatus.VERIFICATION_REQUIRED or session_status == LinkedInSessionStatus.CHECKPOINT:
            # Verification required — create pending session for manual intervention
            logger.warning("⚠️ LinkedIn requires verification for relogin")
            pw, browser, context, page, user_agent = session_resources

            session_id = session_manager.create_session(
                linkedin_email=account.linkedin_email,
                owner_email=owner_email,
                label=account.label,
                pw=pw,
                browser=browser,
                context=context,
                page=page,
                encrypted_password=account.encrypted_password,
                user_agent=user_agent,
                profile_lock=lock,
            )
            lock = None
            pw = context = page = None
            pending_session_created = True

            account.status = LinkedInAccountStatus.PENDING_VERIFICATION
            await db.commit()
            await db.refresh(account)

            # Send email notification
            try:
                from core.email import send_verification_email
                await send_verification_email(
                    email=owner_email,
                    code="MANUAL_VERIFICATION_REQUIRED"
                )
                logger.info("✅ Email notification sent to user: %s", owner_email)
            except Exception as email_error:
                logger.error(f"❌ Failed to send email notification: {str(email_error)}")

            return SessionVerificationResponse(
                status="PENDING_VERIFICATION",
                message=f"LinkedIn {'checkpoint detected' if session_status == LinkedInSessionStatus.CHECKPOINT else 'requires verification'}. Session ID: {session_id}. Use this to submit verification code.",
                account=LinkedInAccountResponse.model_validate(account),
                requires_manual_verification=True,
                session_id=session_id
            )

        else:
            # Relogin failed — linkedin_login already closed its own browser
            # resources on this path (session_resources is None).
            logger.warning("⚠️ Credential relogin fallback failed with status: %s", session_status.value)

            account.status = LinkedInAccountStatus.FAILED
            await db.commit()
            await db.refresh(account)

            error_message = "Automatic relogin failed"
            if login_error_detail:
                error_message = login_error_detail
            elif session_status == LinkedInSessionStatus.EXPIRED:
                error_message = "Invalid credentials during relogin"
            elif session_status == LinkedInSessionStatus.UNKNOWN:
                error_message = "Unknown error during relogin"

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
        # Only clean up Playwright resources if we didn't create a pending session
        # (pending sessions should stay alive for verification code entry)
        if not pending_session_created:
            if context:
                try:
                    await context.close()
                except:
                    pass
            if pw:
                try:
                    await pw.stop()
                except:
                    pass
            logger.debug("🧹 Playwright resources cleaned up")
        # Release the profile lock unless it was transferred to a pending
        # session (in which case lock is None here).
        release_profile_lock(lock)

"""
Smoke-test endpoint: login to LinkedIn and like the first post on a profile.
FILE: api/v1/test_automation.py

POST /api/v1/test/like-first-post
Body: { "linkedin_email": "...", "linkedin_password": "...", "profile_url": "..." }
Returns: { "visited": bool, "liked_post": bool, "profile_name": str, "post_url": str, "error": str }

NOTE: This endpoint is for internal testing only.
      Remove or gate behind admin auth before any production use.
"""

import asyncio
import logging

# Configure logging to show INFO level messages in the terminal.
# This is useful for debugging this specific test endpoint.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)-8s - %(name)s - %(message)s'
)
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.future import select
from automation.session import load_session_cookies, verify_session, LinkedInSessionStatus
from api.dependencies import get_db
from models.linkedin_account import LinkedInAccount
from sqlalchemy.ext.asyncio import AsyncSession
from automation.browser import launch_browser
from automation.human import human_click, human_type, human_scroll, random_idle_pause
from automation.actions.like_post import visit_profile_and_like_post
from core.logging_config import get_logger, should_log_debug

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/test", tags=["test-automation"])

# import the database session
class LikeTestRequest(BaseModel):
    linkedin_email: str
    linkedin_password: str
    profile_url: str   # The LinkedIn profile whose first post you want to like
                       # Example: "https://www.linkedin.com/in/some-person/"


class LikeTestResponse(BaseModel):
    visited: bool
    liked_post: bool
    profile_name: str | None
    post_url: str | None
    error: str | None


@router.post(
    "/like-first-post",
    response_model=LikeTestResponse,
    summary="[Test] Login to LinkedIn and like first post on a profile",
)
async def test_like_first_post(
    payload: LikeTestRequest,
    db: AsyncSession = Depends(get_db)
    #current_user: User = Depends(require_roles([UserRole.CUSTOMER.value])),
) -> LikeTestResponse:
    """
    Launches a Playwright browser, logs into LinkedIn with the given
    credentials, navigates to the profile, and likes the first post.

    Returns the post URL and like status.
    This is a blocking call — Playwright runs in a thread to avoid
    blocking the FastAPI event loop.
    """
    logger.info(f"🚀 Starting LinkedIn automation test for email: {payload.linkedin_email}")
    logger.info(f"📝 Test parameters - Profile URL: {payload.profile_url}")
    
    try:
        result = await asyncio.wait_for(
            _run_like_test(
                email=payload.linkedin_email,
                password=payload.linkedin_password,
                profile_url=payload.profile_url,
                db= db
            ),
            # TODO: Decrease timeout for production (currently 300s for local network development)
            timeout=300,  # 5 minute timeout max
        )
        logger.info(f"✅ LinkedIn automation test completed successfully")
        return LikeTestResponse(**result)
    except asyncio.TimeoutError:
        logger.error(f"⏱️ LinkedIn automation test timed out after 2 minutes")
        return LikeTestResponse(
            visited=False, liked_post=False,
            profile_name=None,
            post_url=None,
            error="Timeout — Playwright took longer than 2 minutes",
        )
    except Exception as e:
        logger.error(f"❌ LinkedIn automation test failed with exception: {str(e)}", exc_info=True)
        return LikeTestResponse(
            visited=False, 
            liked_post=False,
            profile_name=None,
            post_url=None,
            error=str(e),
        )


async def _run_like_test(email: str, password: str, profile_url: str, db: AsyncSession) -> dict:
    """
    Full Playwright flow:
      1. Launch stealth browser
      2. Load session from cookies
      3. Navigate to profile
      4. Like first post
      5. Return result dict
    """
   
    # ── Step 1: Get the account based on email  ───────────────────────────
    logger.info(f"🔍 Looking up LinkedIn account in database for email: {email}")
    result = await db.execute(
            select(LinkedInAccount).where(LinkedInAccount.linkedin_email == email)
        )
    account = result.scalars().first()
        
    if not account:
            logger.warning(f"⚠️ No LinkedIn account found in database for email: {email}")
            return {
                "visited": False, "liked_post": False, "profile_name": None, "post_url": None,
                "error": f"No LinkedIn account found in DB for {email}",
            }
    
    logger.info("🌐 Launching Playwright browser...")
    # Use saved User-Agent for session consistency
    user_agent = account.user_agent if account else None
    pw, browser, context, page, _ = await launch_browser(user_agent=user_agent)
    logger.info("✅ Browser launched successfully")

    try:
        #  Direct login 

        # ── Step 1: Navigate to LinkedIn login page ───────────────────────────
        # The code below is commented out because the primary test path uses cookies.
        # await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        # await random_idle_pause(2, 4)
        #
        # # ── Step 2: Fill in email ──────────────────────────────────────────
        # await human_type(page, "#username", email)
        # await random_idle_pause(0.5, 1.5)
        #
        # # ── Step 3: Fill in password ───────────────────────────────────────
        # await human_type(page, "#password", password)
        # await random_idle_pause(0.8, 2.0)
        #
        # # ── Step 4: Click Sign In ──────────────────────────────────────────
        # await human_click(page, "button[type='submit']")
        # await random_idle_pause(4, 7)  # Wait for redirect + page load

        # ── Step 5: Verify login succeeded ───────────────────────────────────
        # The code below uses session cookies instead of direct login.

        # using cookies from an account 

      
       
        logger.info(f"✅ Found LinkedIn account in database. Cookie updated at: {account.cookies_updated_at}")
        if should_log_debug():
            logger.debug(f"Has encrypted cookies: {bool(account.encrypted_cookies)}")
            logger.debug(f"Account status: {account.status}")
            logger.debug(f"Cookie updated at: {account.cookies_updated_at}")
        
        # load cookies from the account
        logger.info("🍪 Loading session cookies from database...")
        cookies_loaded = await load_session_cookies(context, account)
        if not cookies_loaded:
            logger.warning("⚠️ No session cookies found in database for this account")
            return {
                "visited": False, "liked_post": False, "profile_name": None, "post_url": None,
                "error": "No session cookies found for this account. Please add the account first.",
            }
        logger.info(f"✅ Session cookies loaded successfully. Total cookies: {len(await context.cookies())}")
        if should_log_debug():
            logger.debug(f"Total cookies loaded: {len(await context.cookies())}")
        
        # verify session
        logger.info("🔐 Verifying session validity by navigating to feed...")
        verification_result = await verify_session(page)
        logger.debug(f"Current URL after verification: {page.url}")
        logger.debug(f"Session status: {verification_result.status.value} - {verification_result.message}")

        if verification_result.status != LinkedInSessionStatus.VALID:
            logger.error(f"❌ Session verification failed. Status: {verification_result.status.value}")
            return {
                "visited": False, "liked_post": False, "profile_name": None, "post_url": None,
                "error": f"Session verification failed: {verification_result.message}. Status: {verification_result.status.value}",
            }
        logger.info("✅ Session verification successful - user is logged in")

        # ── Step 6: Visit profile and like first post ─────────────────────────
        logger.info(f"👤 Navigating to profile: {profile_url}")
        result = await visit_profile_and_like_post(page, profile_url)

        # Grab the post URL (we're on the activity page at this point)
        post_url = page.url if "/recent-activity" in page.url else None

        logger.info(f"📊 Test completed - Visited: {result['visited']}, Liked: {result['liked_post']}, Post URL: {post_url}")

        return {
            "visited":      result["visited"],
            "liked_post":   result["liked_post"],
            "profile_name": result["profile_name"],
            "post_url":     post_url,
            "error":        result["error"],
        }

    finally:
        # Always close browser — no zombie processes
        logger.info("🧹 Closing browser and cleaning up resources...")
        await context.close()
        await browser.close()
        await pw.stop()
        logger.info("✅ Cleanup complete")
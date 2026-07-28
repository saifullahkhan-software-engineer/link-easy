"""
Celery tasks for campaign drip sequence execution.
FILE: worker/tasks/campaign_tasks.py

Dynamic task execution based on campaign_steps table.
Each task executes a specific step for a lead, then schedules the next step
based on the campaign's step configuration.
"""
import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from celery import shared_task
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
 
from core.config import settings
from core.security import decrypt_credential
from core.logging_config import get_logger
from worker.celery_app import celery_app
from worker.rate_limit import check_and_increment
from worker.playwright_semaphore import acquire_playwright_session
from automation.browser import launch_browser
from automation.session import load_session_state, save_session_state, verify_session, LinkedInSessionStatus
from automation.actions.visit_profile import (
    visit_profile,
    like_recent_post,
    visit_profile_and_like_post,
)
from automation.actions.connect import send_connection_request
from automation.actions.message import send_message
from models.lead import Lead, LeadStatus
from models.campaign import Campaign, CampaignStep, CampaignStepType, CampaignStatus
from models.campaign_job import CampaignJob, JobStatus
from models.linkedin_account import LinkedInAccount, LinkedInAccountStatus
from models.user import User  # Import User model for foreign key metadata


logger = get_logger(__name__)


# ── Session Configuration ─────────────────────────────────────────────────────
MAX_ACTIONS_PER_SESSION = 20  # Maximum leads to process per session run
SESSION_DURATION_MIN = 15    # Minimum session duration in minutes
SESSION_DURATION_MAX = 20   # Maximum session duration in minutes
INTERSTITIAL_ACTION_RATE = 0.3  # 30% chance of interstitial action between leads


# ── Custom Exceptions ───────────────────────────────────────────────────────
class SessionFailureException(Exception):
    """Raised when LinkedIn session is invalid/expired/checkpoint - should suspend account without retry."""
    pass
 
# ── Sync DB session for Celery (psycopg2, NOT asyncpg) ───────────────────────
_sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
_engine   = create_engine(_sync_url, pool_pre_ping=True)
SyncSession = sessionmaker(bind=_engine)
 
 
def get_sync_db():
    """Context manager for a sync SQLAlchemy session in Celery tasks."""
    from contextlib import contextmanager
    @contextmanager
    def _session():
        session = SyncSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    return _session()
 
 
def _jitter_hours(base_hours: int) -> float:
    """Add ±2 hour jitter to scheduled delays. No two leads fire at the exact same time."""
    return base_hours + random.uniform(-2, 2)


def _schedule_next(task_name: str, args: list, delay_hours: int, campaign_id: str) -> None:
    """Legacy wrapper for backward compatibility. Delegates to new step-based scheduling."""
    # This is kept for legacy tasks but should not be used in new code
    # The new system uses _schedule_next_step which is step-aware
    eta = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
    celery_app.send_task(task_name, args=args, eta=eta)
 
 
def _schedule_next_step(lead_id: str, campaign_id: str, current_step_order: int) -> None:
    """Schedule the next step in the campaign sequence."""
    with get_sync_db() as db:
        # Get the next step in sequence
        next_step = db.query(CampaignStep).filter(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == current_step_order + 1
        ).first()
        
        if not next_step:
            # No more steps - campaign complete for this lead
            lead = db.query(Lead).get(lead_id)
            if lead:
                lead.status = LeadStatus.COMPLETE
                lead.completed_at = datetime.now(timezone.utc)
            return
        
        # Check if step has a condition that needs to be met
        if next_step.condition:
            lead = db.query(Lead).get(lead_id)
            if next_step.condition == "accepted" and lead.status != LeadStatus.ACCEPTED:
                # Skip this step, try the next one
                _schedule_next_step(lead_id, campaign_id, current_step_order + 1)
                return
            if next_step.condition == "not_accepted" and lead.status == LeadStatus.ACCEPTED:
                # Skip this step, try the next one
                _schedule_next_step(lead_id, campaign_id, current_step_order + 1)
                return
        
        # Schedule the next step with its delay
        delay_hours = next_step.delay_hours
        eta = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
        celery_app.send_task(
            "tasks.execute_campaign_step",
            args=[lead_id, campaign_id, next_step.step_order],
            eta=eta
        )
 
 
# ═══════════════════════════════════════════════════════════════════════════════
#  GENERIC STEP EXECUTOR
# ═══════════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, max_retries=3, name="tasks.execute_campaign_step")
def execute_campaign_step(self, lead_id: str, campaign_id: str, step_order: int):
    """Execute a campaign step for a lead based on campaign_steps table."""
    with get_sync_db() as db:
        lead = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        step = db.query(CampaignStep).filter(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == step_order
        ).first()
        
        if not lead or not campaign or not step:
            return {"status": "skipped", "reason": "lead, campaign, or step not found"}
        
        account = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
        if not account or account.status != LinkedInAccountStatus.ACTIVE:
            # Create CampaignJob record for audit trail
            job_id = str(uuid.uuid4())
            job = CampaignJob(
                id=job_id,
                campaign_id=campaign_id,
                lead_id=lead_id,
                step_type=step.step_type.value if step else "unknown",
                status=JobStatus.SKIPPED,
                error_message=f"Account not active. Status: {account.status if account else 'not found'}",
                celery_task_id=self.request.id,
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc)
            )
            db.add(job)
            db.commit()
            
            # Re-enqueue the task for 1 hour later
            from datetime import timedelta
            eta = datetime.now(timezone.utc) + timedelta(hours=1)
            celery_app.send_task(
                "tasks.execute_campaign_step",
                args=[lead_id, campaign_id, step_order],
                eta=eta
            )
            
            return {"status": "skipped", "reason": "account not active", "requeued_at": eta.isoformat()}
        
        # Update lead's current step
        lead.current_step = step_order
        
        # Create job record
        job_id = str(uuid.uuid4())
        job = CampaignJob(
            id=job_id,
            campaign_id=campaign_id,
            lead_id=lead_id,
            step_type=step.step_type.value,
            status=JobStatus.RUNNING,
            celery_task_id=self.request.id,
            started_at=datetime.now(timezone.utc)
        )
        db.add(job)
        db.flush()
        
        result = None  # Initialize result to avoid UnboundLocalError
        
        try:
            # Execute the appropriate action based on step type
            result = asyncio.run(_execute_step_action(step.step_type, account, lead, campaign))
            
            # Update job status
            job.status = JobStatus.DONE
            job.completed_at = datetime.now(timezone.utc)
            
            # Update lead status based on step type
            _update_lead_status(lead, step.step_type)
            
            # Schedule next step - wrap in try/except to separate scheduling failures
            try:
                _schedule_next_step(lead_id, campaign_id, step_order)
            except Exception as scheduling_exc:
                # Log scheduling failure but don't fail the step itself
                logger.error(f"Failed to schedule next step: {scheduling_exc}")
                job.error_message = f"Step completed but failed to schedule next: {scheduling_exc}"
            
            db.commit()
            return result
            
        except SessionFailureException as exc:
            # Session failure - suspend account and stop campaign without retry
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            
            # Suspend the LinkedIn account to prevent LinkedIn bot detection
            account.status = LinkedInAccountStatus.SUSPENDED
            account.updated_at = datetime.now(timezone.utc)
            
            # Mark lead as failed
            lead.status = LeadStatus.FAILED
            
            # Do NOT retry - this prevents LinkedIn from detecting bot activity
            # The campaign will stop for this account
            db.commit()
            return {"status": "failed", "reason": "session_failure", "error": str(exc)}
            
        except Exception as exc:
            # Other exceptions - retry with backoff
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            lead.status = LeadStatus.FAILED
            db.commit()
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))


@celery_app.task(name="tasks.reconcile_stalled_leads")
def reconcile_stalled_leads():
    """Find stalled leads and re-enqueue them for processing.
    
    Runs every 15 minutes via Celery Beat to find leads where:
    - Parent Campaign is ACTIVE
    - Lead is still PENDING with current_step == 0
    - created_at is older than 30 minutes
    - No existing QUEUED or RUNNING CampaignJob for step 1
    
    This prevents leads from getting stuck if the worker fails or tasks are lost.
    """
    logger.info("🔍 Reconciling stalled leads...")
    
    with get_sync_db() as db:
        from datetime import datetime, timedelta, timezone
        
        # Find stalled leads
        thirty_minutes_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        
        stalled_leads = db.query(Lead).join(Campaign).filter(
            Campaign.status == CampaignStatus.ACTIVE,
            Lead.status == LeadStatus.PENDING,
            Lead.current_step == 0,
            Lead.created_at < thirty_minutes_ago
        ).all()
        
        logger.info(f"📊 Found {len(stalled_leads)} potentially stalled leads")
        
        requeued_count = 0
        for lead in stalled_leads:
            # Check if there's already a QUEUED or RUNNING job for step 1
            existing_job = db.query(CampaignJob).filter(
                CampaignJob.lead_id == lead.id,
                CampaignJob.campaign_id == lead.campaign_id,
                CampaignJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
            ).first()
            
            if existing_job:
                logger.debug(f"⏭️ Lead {lead.id} already has {existing_job.status} job, skipping")
                continue
            
            # Check if there's a recent DONE job for step 1 (within last hour)
            recent_done_job = db.query(CampaignJob).filter(
                CampaignJob.lead_id == lead.id,
                CampaignJob.campaign_id == lead.campaign_id,
                CampaignJob.status == JobStatus.DONE,
                CampaignJob.completed_at > thirty_minutes_ago
            ).first()
            
            if recent_done_job:
                logger.debug(f"⏭️ Lead {lead.id} has recent DONE job, skipping")
                continue
            
            # Re-enqueue the task for step 1
            try:
                celery_app.send_task(
                    "tasks.execute_campaign_step",
                    args=[lead.id, lead.campaign_id, 1],
                    eta=datetime.now(timezone.utc) + timedelta(minutes=5)
                )
                requeued_count += 1
                logger.info(f"🔄 Re-enqueued lead {lead.id} for step 1")
            except Exception as e:
                logger.error(f"❌ Failed to re-enqueue lead {lead.id}: {e}")
        
        logger.info(f"✅ Reconciliation complete: {requeued_count} leads re-enqueued")
        return {"requeued_count": requeued_count, "total_stalled": len(stalled_leads)}


async def _execute_step_action(step_type, account, lead, campaign, page=None) -> dict:
    """
    Execute the appropriate action for a step type.
    If page is provided, uses existing browser context (session-based).
    If page is None, creates new browser context (legacy per-lead tasks).
    """
    if page:
        # Session-based: use existing page/context
        return await _execute_step_with_page(step_type, account, lead, campaign, page)
    else:
        # Legacy: create new browser context per action
        if step_type == CampaignStepType.VISIT_PROFILE:
            return await _run_visit(account, lead)
        elif step_type == CampaignStepType.LIKE_POST:
            return await _run_like(account, lead)
        elif step_type == CampaignStepType.VISIT_AND_LIKE:
            return await _run_visit_and_like(account, lead)
        elif step_type == CampaignStepType.SEND_CONNECTION:
            return await _run_connect(account, lead, campaign)
        elif step_type in [CampaignStepType.SEND_MESSAGE, CampaignStepType.FOLLOW_UP_IF_PENDING, CampaignStepType.THANKS_IF_ACCEPTED]:
            templates = campaign.message_templates or []
            if step_type == CampaignStepType.SEND_MESSAGE:
                message_text = templates[0] if templates else "Hi {{first_name}}, great to connect!"
            elif step_type == CampaignStepType.FOLLOW_UP_IF_PENDING:
                message_text = templates[1] if len(templates) > 1 else "Hi {{first_name}}, just wanted to follow up!"
            else:  # THANKS_IF_ACCEPTED
                message_text = templates[2] if len(templates) > 2 else "Thanks for connecting, {{first_name}}!"
            return await _run_message(account, lead, message_text)
        else:
            raise Exception(f"Unknown step type: {step_type}")


async def _execute_step_with_page(step_type, account, lead, campaign, page) -> dict:
    """
    Execute step action using existing browser context (session-based).
    """
    if step_type == CampaignStepType.VISIT_PROFILE:
        return await visit_profile(page, lead.linkedin_url)
    elif step_type == CampaignStepType.LIKE_POST:
        return await like_recent_post(page, lead.linkedin_url)
    elif step_type == CampaignStepType.VISIT_AND_LIKE:
        return await visit_profile_and_like_post(page, lead.linkedin_url)
    elif step_type == CampaignStepType.SEND_CONNECTION:
        return await send_connection_request(
            page, lead.linkedin_url,
            first_name=lead.first_name,
            note_template=campaign.connection_note_template,
        )
    elif step_type in [CampaignStepType.SEND_MESSAGE, CampaignStepType.FOLLOW_UP_IF_PENDING, CampaignStepType.THANKS_IF_ACCEPTED]:
        templates = campaign.message_templates or []
        if step_type == CampaignStepType.SEND_MESSAGE:
            message_text = templates[0] if templates else "Hi {{first_name}}, great to connect!"
        elif step_type == CampaignStepType.FOLLOW_UP_IF_PENDING:
            message_text = templates[1] if len(templates) > 1 else "Hi {{first_name}}, just wanted to follow up!"
        else:  # THANKS_IF_ACCEPTED
            message_text = templates[2] if len(templates) > 2 else "Thanks for connecting, {{first_name}}!"
        return await send_message(page, lead.linkedin_url, message_text, lead.first_name)
    else:
        raise Exception(f"Unknown step type: {step_type}")


def _update_lead_status(lead, step_type) -> None:
    """Update lead status based on executed step type."""
    if step_type == CampaignStepType.VISIT_PROFILE or step_type == CampaignStepType.LIKE_POST or step_type == CampaignStepType.VISIT_AND_LIKE:
        lead.status = LeadStatus.VISITING
    elif step_type == CampaignStepType.SEND_CONNECTION:
        lead.status = LeadStatus.REQUESTED
        lead.connection_sent_at = datetime.now(timezone.utc)
    elif step_type == CampaignStepType.SEND_MESSAGE:
        lead.status = LeadStatus.MESSAGED
    lead.last_action_at = datetime.now(timezone.utc)


# ═══════════════════════════════════════════════════════════════════════════════
#  LEGACY STEP TASKS (Kept for backward compatibility, will be removed)
# ═══════════════════════════════════════════════════════════════════════════════
 
@celery_app.task(bind=True, max_retries=3, name="tasks.step1_visit_and_like")
def step1_visit_and_like(self, lead_id: str, campaign_id: str):
    with get_sync_db() as db:
        lead     = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        account  = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
 
        if not lead or not account or account.status != LinkedInAccountStatus.ACTIVE:
            return {"status": "skipped", "reason": "lead or account not valid"}
 
        # Rate limit check
        if not check_and_increment(account.owner_email, "visit_profile",
                                    campaign.daily_visit_limit):
            # Re-queue for tomorrow at a random time
            _schedule_next("tasks.step1_visit_and_like", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "rate_limited", "requeued": True}
 
        job_id = str(uuid.uuid4())
        job = CampaignJob(id=job_id, campaign_id=campaign_id, lead_id=lead_id,
                          step_type="visit_and_like", status=JobStatus.RUNNING,
                          celery_task_id=self.request.id,
                          started_at=datetime.now(timezone.utc))
        db.add(job)
        db.flush()
 
        try:
            result = asyncio.run(_run_visit(account, lead))
 
            lead.status = LeadStatus.VISITING
            lead.last_action_at = datetime.now(timezone.utc)
            job.status = JobStatus.DONE
            job.completed_at = datetime.now(timezone.utc)
 
            # Schedule Step 2 using campaign's step_delay_hours
            _schedule_next("tasks.step2_send_connection", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
 
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            lead.status = LeadStatus.FAILED
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
async def _run_visit(account, lead) -> dict:
    """Async wrapper for the Playwright visit-only action (no like)."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        # Load storage state before creating context
        logger.info(f"🔓 Loading storage state for account {account.linkedin_email}")
        logger.info(f"📅 Storage state last updated at: {account.cookies_updated_at}")
        storage_state = await load_session_state(account)
        if not storage_state:
            raise SessionFailureException("No session state — account needs Playwright login first")

        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host,
            proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
            user_agent=account.user_agent,  # Use saved user agent for consistency
            storage_state=storage_state,  # Pass storage state to context
        )
        try:
            logger.info("🔐 Verifying session validity...")
            verification = await verify_session(page)
            logger.info(f"🔍 Verification result: {verification.status.value} - {verification.message}")
            
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            
            logger.info(f"✅ Session valid, visiting profile: {lead.linkedin_url}")
            result = await visit_profile(page, lead.linkedin_url)
            
            # Save updated storage state after successful action
            await save_session_state(context, account, user_agent)
            logger.info("💾 Storage state saved after successful visit")
            
            return result
        finally:
            await context.close()
            await browser.close()
            await pw.stop()


async def _run_like(account, lead) -> dict:
    """Async wrapper for the Playwright like-only action (no profile visit)."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        # Load storage state before creating context
        storage_state = await load_session_state(account)
        if not storage_state:
            raise SessionFailureException("No session state — account needs Playwright login first")

        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host,
            proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
            user_agent=account.user_agent,
            storage_state=storage_state,
        )
        try:
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            result = await like_recent_post(page, lead.linkedin_url)
            
            # Save updated storage state after successful action
            await save_session_state(context, account, user_agent)
            logger.info("💾 Storage state saved after successful like")
            
            return result
        finally:
            await context.close()
            await browser.close()
            await pw.stop()


async def _run_visit_and_like(account, lead) -> dict:
    """Async wrapper for combined visit profile and like post action."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        # Load storage state before creating context
        logger.info(f"🔓 Loading storage state for account {account.linkedin_email}")
        logger.info(f"📅 Storage state last updated at: {account.cookies_updated_at}")
        storage_state = await load_session_state(account)
        if not storage_state:
            raise SessionFailureException("No session state — account needs Playwright login first")

        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host,
            proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
            user_agent=account.user_agent,  # Use saved user agent for consistency
            storage_state=storage_state,
        )
        try:
            logger.info("🔐 Verifying session validity...")
            verification = await verify_session(page)
            logger.info(f"🔍 Verification result: {verification.status.value} - {verification.message}")
            
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            
            logger.info(f"✅ Session valid, visiting profile and liking post: {lead.linkedin_url}")
            result = await visit_profile_and_like_post(page, lead.linkedin_url)
            
            # Save updated storage state after successful action
            await save_session_state(context, account, user_agent)
            logger.info("💾 Storage state saved after successful visit and like")
            
            return result
        finally:
            await context.close()
            await browser.close()
            await pw.stop()


# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 2 — Send Connection Request
# ═══════════════════════════════════════════════════════════════════════════════
 
@celery_app.task(bind=True, max_retries=3, name="tasks.step2_send_connection")
def step2_send_connection(self, lead_id: str, campaign_id: str):
    with get_sync_db() as db:
        lead     = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        account  = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
 
        if not check_and_increment(account.owner_email, "send_connection",
                                    campaign.daily_connection_limit):
            _schedule_next("tasks.step2_send_connection", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "rate_limited"}
 
        try:
            result = asyncio.run(_run_connect(account, lead, campaign))
 
            lead.status = LeadStatus.REQUESTED
            lead.connection_sent_at = datetime.now(timezone.utc)
            lead.last_action_at = datetime.now(timezone.utc)
 
            # Schedule Step 3 using campaign's step_delay_hours
            _schedule_next("tasks.step3_send_message", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
 
        except SessionFailureException as exc:
            # Session failure - suspend account without retry
            account.status = LinkedInAccountStatus.SUSPENDED
            account.updated_at = datetime.now(timezone.utc)
            lead.status = LeadStatus.FAILED
            db.commit()
            # Do NOT retry to prevent LinkedIn bot detection
            

        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
async def _run_connect(account, lead, campaign) -> dict:
    from worker.playwright_semaphore import acquire_playwright_session
    
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")
        
        # Load storage state before creating context
        storage_state = await load_session_state(account)
        if not storage_state:
            raise SessionFailureException("No session state — account needs Playwright login first")
        
        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host, proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
            user_agent=account.user_agent,
            storage_state=storage_state,
        )
        try:
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            result = await send_connection_request(
                page, lead.linkedin_url,
                first_name=lead.first_name,
                note_template=campaign.connection_note_template,
            )
            
            # Save updated storage state after successful action
            await save_session_state(context, account, user_agent)
            logger.info("💾 Storage state saved after successful connection request")
            
            return result
        finally:
            await context.close()
            await browser.close()
            await pw.stop()
 
 
# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 3 — Send Intro Message (only if accepted)
# ═══════════════════════════════════════════════════════════════════════════════
 
@celery_app.task(bind=True, max_retries=3, name="tasks.step3_send_message")
def step3_send_message(self, lead_id: str, campaign_id: str):
    with get_sync_db() as db:
        lead     = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        account  = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
 
        # Only send if connection was accepted
        if lead.status != LeadStatus.ACCEPTED:
            # Not yet accepted — schedule step 4 (follow-up check)
            _schedule_next("tasks.step4_followup_if_pending", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "not_accepted_yet", "escalated_to_step4": True}
 
        if not check_and_increment(account.owner_email, "send_message",
                                    campaign.daily_message_limit):
            _schedule_next("tasks.step3_send_message", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "rate_limited"}
 
        templates = campaign.message_templates or []
        message_text = templates[0] if templates else "Hi {{first_name}}, great to connect!"
 
        try:
            result = asyncio.run(_run_message(account, lead, message_text))
            lead.status = LeadStatus.MESSAGED
            lead.last_action_at = datetime.now(timezone.utc)
            # Schedule thanks message check for day 5
            _schedule_next("tasks.step5_thanks_if_accepted", [lead_id, campaign_id],
                           delay_hours=48, campaign_id=campaign_id)
        except SessionFailureException as exc:
            # Session failure - suspend account without retry
            account.status = LinkedInAccountStatus.SUSPENDED
            account.updated_at = datetime.now(timezone.utc)
            lead.status = LeadStatus.FAILED
            db.commit()
            # Do NOT retry to prevent LinkedIn bot detection
            

        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 4 — Follow-up if Pending (not yet accepted)
# ═══════════════════════════════════════════════════════════════════════════════
 
@celery_app.task(bind=True, max_retries=2, name="tasks.step4_followup_if_pending")
def step4_followup_if_pending(self, lead_id: str, campaign_id: str):
    with get_sync_db() as db:
        lead     = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        account  = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
 
        # If they accepted in the meantime, skip follow-up
        if lead.status == LeadStatus.ACCEPTED:
            _schedule_next("tasks.step5_thanks_if_accepted", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "accepted_skip_followup"}
 
        if not check_and_increment(account.owner_email, "send_message",
                                    campaign.daily_message_limit):
            _schedule_next("tasks.step4_followup_if_pending", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "rate_limited"}
 
        templates = campaign.message_templates or []
        # Index 1 = follow-up template (if campaign has multiple templates)
        message_text = templates[1] if len(templates) > 1 else "Hi {{first_name}}, just wanted to follow up on my connection request!"
 
        try:
            result = asyncio.run(_run_message(account, lead, message_text))
            lead.last_action_at = datetime.now(timezone.utc)
        except SessionFailureException as exc:
            # Session failure - suspend account without retry
            account.status = LinkedInAccountStatus.SUSPENDED
            account.updated_at = datetime.now(timezone.utc)
            lead.status = LeadStatus.FAILED
            db.commit()
            # Do NOT retry to prevent LinkedIn bot detection
            

        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
# ═══════════════════════════════════════════════════════════════════════════════
#  STEP 5 — Thanks Message if Accepted
# ═══════════════════════════════════════════════════════════════════════════════
 
@celery_app.task(bind=True, max_retries=2, name="tasks.step5_thanks_if_accepted")
def step5_thanks_if_accepted(self, lead_id: str, campaign_id: str):
    with get_sync_db() as db:
        lead     = db.query(Lead).get(lead_id)
        campaign = db.query(Campaign).get(campaign_id)
        account  = db.query(LinkedInAccount).filter_by(linkedin_email=campaign.account_email).first()
 
        if lead.status != LeadStatus.ACCEPTED:
            return {"status": "not_accepted_no_action"}
 
        if not check_and_increment(account.owner_email, "send_message",
                                    campaign.daily_message_limit):
            _schedule_next("tasks.step5_thanks_if_accepted", [lead_id, campaign_id],
                           delay_hours=24, campaign_id=campaign_id)
            return {"status": "rate_limited"}
 
        templates = campaign.message_templates or []
        message_text = templates[2] if len(templates) > 2 else "Thanks for connecting, {{first_name}}! Looking forward to staying in touch."
 
        try:
            result = asyncio.run(_run_message(account, lead, message_text))
            lead.last_action_at = datetime.now(timezone.utc)
        except SessionFailureException as exc:
            # Session failure - suspend account without retry
            account.status = LinkedInAccountStatus.SUSPENDED
            account.updated_at = datetime.now(timezone.utc)
            lead.status = LeadStatus.FAILED
            db.commit()
            # Do NOT retry to prevent LinkedIn bot detection
            

        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
async def _run_message(account, lead, message_text: str) -> dict:
    from worker.playwright_semaphore import acquire_playwright_session
    
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")
        
        # Load storage state before creating context
        storage_state = await load_session_state(account)
        if not storage_state:
            raise SessionFailureException("No session state — account needs Playwright login first")
        
        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host, proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
            user_agent=account.user_agent,
            storage_state=storage_state,
        )
        try:
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            result = await send_message(page, lead.linkedin_url, message_text, lead.first_name)
            
            # Save updated storage state after successful action
            await save_session_state(context, account, user_agent)
            logger.info("💾 Storage state saved after successful message send")
            
            return result
        finally:
            await context.close()
            await browser.close()
            await pw.stop()


# ═══════════════════════════════════════════════════════════════════════════════
#  ACCOUNT SESSION TASK (NEW ARCHITECTURE)
# ═══════════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="tasks.run_account_session")
def run_account_session(self, account_email: str):
    """
    Single task per account that processes all due leads across all campaigns.
    Opens one browser/context, processes leads with human-like timing, and saves state.
    """
    import redis
    from core.config import settings
    
    # Redis lock to prevent overlapping sessions for the same account
    redis_client = redis.from_url(settings.REDIS_URL)
    lock_key = f"session_lock:{account_email}"
    lock = redis_client.lock(lock_key, timeout=7200)  # 2 hour timeout
    
    if not lock.acquire(blocking=False):
        logger.warning(f"⚠️ Session already running for account {account_email}, skipping")
        return {"status": "skipped", "reason": "session_already_running"}
    
    try:
        with get_sync_db() as db:
            # Get the account
            account = db.query(LinkedInAccount).filter_by(linkedin_email=account_email).first()
            if not account or account.status != LinkedInAccountStatus.ACTIVE:
                logger.warning(f"⚠️ Account {account_email} not found or not active")
                return {"status": "skipped", "reason": "account_not_active"}
            
            # Query all due leads across all ACTIVE campaigns for this account
            # For leads that haven't started (current_step is NULL/0), match step 1
            # For leads that have started, match their current step
            from sqlalchemy import or_
            due_leads = db.query(Lead, Campaign, CampaignStep).join(
                Campaign, Lead.campaign_id == Campaign.id
            ).join(
                CampaignStep, Campaign.id == CampaignStep.campaign_id
            ).filter(
                Campaign.account_email == account_email,
                Campaign.status == CampaignStatus.ACTIVE,
                Lead.status.in_([LeadStatus.PENDING, LeadStatus.VISITING, LeadStatus.REQUESTED, LeadStatus.ACCEPTED]),
                or_(
                    (Lead.current_step == None) & (CampaignStep.step_order == 1),
                    (Lead.current_step == 0) & (CampaignStep.step_order == 1),
                    Lead.current_step == CampaignStep.step_order
                )
            ).all()
            
            if not due_leads:
                logger.info(f"✅ No due leads found for account {account_email}")
                return {"status": "completed", "leads_processed": 0}
            
            # Cap the number of leads per session
            due_leads = due_leads[:MAX_ACTIONS_PER_SESSION]
            logger.info(f"📊 Processing {len(due_leads)} due leads for account {account_email}")
            
            # Calculate target session duration and per-action dwell time
            target_duration_minutes = random.randint(SESSION_DURATION_MIN, SESSION_DURATION_MAX)
            per_action_seconds = (target_duration_minutes * 60) / len(due_leads)
            logger.info(f"⏱️ Target session duration: {target_duration_minutes} minutes, ~{per_action_seconds:.0f}s per action")
            
            # Process leads in async loop
            results = asyncio.run(_process_leads_session(
                account, due_leads, per_action_seconds, db
            ))
            
            logger.info(f"✅ Session completed for account {account_email}. Processed {len(results)} leads.")
            
            # Calculate next session delay based on the current step's delay_hours
            # Use the delay_hours from the step that was just processed (step 1 typically)
            # If delay_hours is 0 or not set, default to 2 hours minimum
            if due_leads:
                # Get the step that was processed (first lead's step)
                _, _, current_step = due_leads[0]
                delay_hours = current_step.delay_hours if current_step.delay_hours and current_step.delay_hours > 0 else 2
            else:
                delay_hours = 2  # Default if no leads processed
            
            next_run_delay = int(delay_hours * 3600)
            self.apply_async(
                args=[account_email],
                countdown=next_run_delay,
                time_limit=7200,
                soft_time_limit=6600
            )
            logger.info(f"📅 Next session scheduled in {delay_hours} hours (based on step delay_hours)")
            
            return {
                "status": "completed",
                "leads_processed": len(results),
                "session_duration_minutes": target_duration_minutes
            }
    
    except Exception as exc:
        logger.error(f"❌ Session failed for account {account_email}: {exc}")
        raise self.retry(exc=exc, countdown=random.randint(600, 1800))
    
    finally:
        try:
            lock.release()
        except Exception:
            # Lock may have expired or been released already (e.g., during retry)
            pass


async def _process_leads_session(account, due_leads, per_action_seconds, db):
    """
    Async function that processes leads in a single browser session.
    """
    # Load storage state before creating context
    logger.info(f"🔓 Loading storage state for account {account.linkedin_email}")
    storage_state = await load_session_state(account)
    if not storage_state:
        raise SessionFailureException("No session state — account needs Playwright login first")
    
    # Launch browser with storage state
    user_agent = account.user_agent
    pw, browser, context, page, actual_user_agent = await launch_browser(
        proxy_host=account.proxy_host,
        proxy_port=account.proxy_port,
        proxy_user=account.proxy_username,
        proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        user_agent=user_agent,
        storage_state=storage_state,
    )
    
    results = []
    
    try:
        # Verify session once at start
        verification = await verify_session(page)
        if verification.status != LinkedInSessionStatus.VALID:
            raise SessionFailureException(
                f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                f"Account will be suspended to prevent LinkedIn bot detection."
            )
        logger.info("✅ Session valid, starting lead processing")
        
        for i, (lead, campaign, step) in enumerate(due_leads):
            try:
                # If lead hasn't started (current_step is NULL/0), assign to step 1
                if lead.current_step is None or lead.current_step == 0:
                    lead.current_step = 1
                    db.flush()
                
                # Ensure we're processing the correct step for this lead
                if step.step_order != lead.current_step:
                    logger.warning(f"⚠️ Step mismatch for lead {lead.id}: step_order={step.step_order}, current_step={lead.current_step}, skipping")
                    continue
                
                logger.info(f"📋 Processing lead {lead.id} (step {step.step_order}: {step.step_type.value})")
                
                # Execute the appropriate action
                result = await _execute_step_action(step.step_type, account, lead, campaign, page)
                
                # Create CampaignJob record
                job_id = str(uuid.uuid4())
                job = CampaignJob(
                    id=job_id,
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    step_type=step.step_type.value,
                    status=JobStatus.DONE,
                    celery_task_id=None,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc)
                )
                db.add(job)
                
                # Update lead status and current_step
                _update_lead_status(lead, step.step_type)
                lead.current_step = step.step_order + 1
                lead.last_action_at = datetime.now(timezone.utc)
                
                db.commit()
                results.append({"lead_id": lead.id, "result": result})
                
                # Save storage state after each action
                await save_session_state(context, account, actual_user_agent)
                logger.info(f"💾 Storage state saved after processing lead {lead.id}")
                
                # Interstitial action and dwell before next lead (except last)
                if i < len(due_leads) - 1:
                    await _interstitial_pause(page, per_action_seconds)
            
            except SessionFailureException as exc:
                # Session failure - suspend account and stop session
                account.status = LinkedInAccountStatus.SUSPENDED
                account.updated_at = datetime.now(timezone.utc)
                lead.status = LeadStatus.FAILED
                db.commit()
                logger.error(f"❌ Session failure, suspending account: {exc}")
                raise
            
            except Exception as exc:
                # Individual lead failure - log and continue
                logger.error(f"❌ Failed to process lead {lead.id}: {exc}")
                job = CampaignJob(
                    id=str(uuid.uuid4()),
                    campaign_id=campaign.id,
                    lead_id=lead.id,
                    step_type=step.step_type.value,
                    status=JobStatus.FAILED,
                    error_message=str(exc),
                    celery_task_id=None,
                    started_at=datetime.now(timezone.utc),
                    completed_at=datetime.now(timezone.utc)
                )
                db.add(job)
                db.commit()
                continue
    
    finally:
        await context.close()
        await browser.close()
        await pw.stop()
    
    return results


async def _interstitial_pause(page, per_action_seconds):
    """
    Random interstitial action and dwell between leads to appear human-like.
    """
    # Random dwell time (50-100% of per_action_seconds)
    dwell_seconds = per_action_seconds * random.uniform(0.5, 1.0)
    logger.info(f"⏸️ Dwell for {dwell_seconds:.0f}s before next action")
    await asyncio.sleep(dwell_seconds)
    
    # 30% chance of interstitial action
    if random.random() < INTERSTITIAL_ACTION_RATE:
        logger.info("🎲 Performing interstitial action...")
        action = random.choice(["feed", "notifications", "scroll"])
        
        if action == "feed":
            await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 5))
        elif action == "notifications":
            await page.goto("https://www.linkedin.com/notifications/", wait_until="domcontentloaded")
            await asyncio.sleep(random.uniform(2, 5))
        elif action == "scroll":
            await page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            await asyncio.sleep(random.uniform(1, 3))
        
        logger.info(f"✅ Interstitial action '{action}' completed")

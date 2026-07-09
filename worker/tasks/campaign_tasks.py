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
from worker.celery_app import celery_app
from worker.rate_limit import check_and_increment
from worker.playwright_semaphore import acquire_playwright_session
from automation.browser import launch_browser
from automation.session import load_session_cookies, save_session_cookies, verify_session, LinkedInSessionStatus
from automation.actions.visit_profile import (
    visit_profile,
    like_recent_post,
    visit_profile_and_like_post,
)
from automation.actions.connect import send_connection_request
from automation.actions.message import send_message
from models.lead import Lead, LeadStatus
from models.campaign import Campaign, CampaignStep, CampaignStepType
from models.campaign_job import CampaignJob, JobStatus
from models.linkedin_account import LinkedInAccount, LinkedInAccountStatus
 
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
            return {"status": "skipped", "reason": "account not active"}
        
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
        
        try:
            # Execute the appropriate action based on step type
            result = _execute_step_action(step.step_type, account, lead, campaign)
            
            # Update job status
            job.status = JobStatus.DONE
            job.completed_at = datetime.now(timezone.utc)
            
            # Update lead status based on step type
            _update_lead_status(lead, step.step_type)
            
            # Schedule next step
            _schedule_next_step(lead_id, campaign_id, step_order)
            
        except Exception as exc:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)
            lead.status = LeadStatus.FAILED
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
    
    return result


def _execute_step_action(step_type, account, lead, campaign) -> dict:
    """Execute the appropriate action for a step type."""
    if step_type == CampaignStepType.VISIT_PROFILE:
        return asyncio.run(_run_visit(account, lead))
    elif step_type == CampaignStepType.LIKE_POST:
        return asyncio.run(_run_like(account, lead))
    elif step_type == CampaignStepType.VISIT_AND_LIKE:
        return asyncio.run(_run_visit(account, lead))  # Combined action
    elif step_type == CampaignStepType.SEND_CONNECTION:
        return asyncio.run(_run_connect(account, lead, campaign))
    elif step_type in [CampaignStepType.SEND_MESSAGE, CampaignStepType.FOLLOW_UP_IF_PENDING, CampaignStepType.THANKS_IF_ACCEPTED]:
        templates = campaign.message_templates or []
        if step_type == CampaignStepType.SEND_MESSAGE:
            message_text = templates[0] if templates else "Hi {{first_name}}, great to connect!"
        elif step_type == CampaignStepType.FOLLOW_UP_IF_PENDING:
            message_text = templates[1] if len(templates) > 1 else "Hi {{first_name}}, just wanted to follow up!"
        else:  # THANKS_IF_ACCEPTED
            message_text = templates[2] if len(templates) > 2 else "Thanks for connecting, {{first_name}}!"
        return asyncio.run(_run_message(account, lead, message_text))
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

        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host,
            proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        )
        try:
            if not await load_session_cookies(context, account):
                raise Exception("No session cookies — account needs Playwright login first")
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                raise Exception(f"LinkedIn session expired — re-login required. Status: {verification.status.value}")
            return await visit_profile(page, lead.linkedin_url)
        finally:
            await context.close()
            await browser.close()
            await pw.stop()


async def _run_like(account, lead) -> dict:
    """Async wrapper for the Playwright like-only action (no profile visit)."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host,
            proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        )
        try:
            if not await load_session_cookies(context, account):
                raise Exception("No session cookies — account needs Playwright login first")
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                raise Exception(f"LinkedIn session expired — re-login required. Status: {verification.status.value}")
            return await like_recent_post(page, lead.linkedin_url)
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
 
        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
async def _run_connect(account, lead, campaign) -> dict:
    from worker.playwright_semaphore import acquire_playwright_session
    
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")
        
        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host, proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        )
        try:
            await load_session_cookies(context, account)
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                raise Exception(f"Session expired. Status: {verification.status.value}")
            return await send_connection_request(
                page, lead.linkedin_url,
                first_name=lead.first_name,
                note_template=campaign.connection_note_template,
            )
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
        except Exception as exc:
            raise self.retry(exc=exc, countdown=random.randint(600, 1800))
 
    return result
 
 
async def _run_message(account, lead, message_text: str) -> dict:
    from worker.playwright_semaphore import acquire_playwright_session
    
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")
        
        pw, browser, context, page, user_agent = await launch_browser(
            proxy_host=account.proxy_host, proxy_port=account.proxy_port,
            proxy_user=account.proxy_username,
            proxy_pass=decrypt_credential(account.proxy_password_enc) if account.proxy_password_enc else None,
        )
        try:
            await load_session_cookies(context, account)
            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                raise Exception(f"Session expired. Status: {verification.status.value}")
            return await send_message(page, lead.linkedin_url, message_text, lead.first_name)
        finally:
            await context.close()
            await browser.close()
            await pw.stop()

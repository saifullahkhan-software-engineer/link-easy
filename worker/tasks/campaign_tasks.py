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
from core.logging_config import get_logger
from worker.celery_app import celery_app
from worker.rate_limit import check_and_increment, warmup_stage_for_account
from worker.playwright_semaphore import acquire_playwright_session
from worker.profile_lock import (
    ProfileInUseError,
    acquire_profile_lock,
    release_profile_lock,
)
from automation.browser import launch_persistent_browser
from automation.session import verify_session, LinkedInSessionStatus
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
CONDITION_RETRY_HOURS = 24  # Re-check conditional steps once per day

# Transient page-load failures (LinkedIn served a blank page while the session
# is still healthy) are retried a bounded number of times, spaced hours apart,
# before the lead is failed permanently.
MAX_PAGE_LOAD_RETRIES = 3
PAGE_LOAD_RETRY_DELAY_HOURS = (2.0, 6.0)


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
        
        # Persist the schedule as well as sending it to Celery. This keeps the
        # next step name/time visible and recoverable if a worker restarts.
        delay_hours = next_step.delay_hours
        eta = datetime.now(timezone.utc) + timedelta(hours=delay_hours)
        lead = db.query(Lead).get(lead_id)
        if lead:
            lead.current_step = next_step.step_order
            lead.next_action_at = eta
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
        
        # Check if campaign is active
        if campaign.status != CampaignStatus.ACTIVE:
            logger.info(f"⏭️ Skipping task for lead {lead_id} because campaign {campaign_id} is {campaign.status}")
            # If paused, we don't re-enqueue; the scheduler or reconciler will pick it up when resumed
            return {"status": "skipped", "reason": f"campaign is {campaign.status}"}
        
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
            # Enforce the daily/warm-up rate limits before opening LinkedIn —
            # the legacy per-step tasks each did this; the generic executor
            # must too or campaign limits are bypassed on this path.
            action_key, action_limit = _rate_limit_for_step(step.step_type, campaign)
            if action_key and not check_and_increment(
                account.owner_email, action_key, action_limit,
                warmup_stage=warmup_stage_for_account(account),
            ):
                job.status = JobStatus.SKIPPED
                job.action_message = f"Daily {_action_label(step.step_type).lower()} limit reached."
                job.completed_at = datetime.now(timezone.utc)
                eta = datetime.now(timezone.utc) + timedelta(hours=random.uniform(18, 26))
                lead.next_action_at = eta
                db.commit()
                celery_app.send_task(
                    "tasks.execute_campaign_step",
                    args=[lead_id, campaign_id, step_order],
                    eta=eta,
                )
                return {"status": "rate_limited", "requeued_at": eta.isoformat()}

            # Execute the appropriate action based on step type
            result = asyncio.run(_execute_step_action(step.step_type, account, lead, campaign))
            
            # Do not mark a browser interaction successful merely because it
            # completed without raising.  In particular, message actions must
            # report ``sent=True`` after LinkedIn clears the composer.
            succeeded, action_message, error_message = _action_outcome(step.step_type, result)
            job.status = JobStatus.DONE if succeeded else JobStatus.FAILED
            job.action_message = action_message
            job.error_message = error_message
            job.completed_at = datetime.now(timezone.utc)

            if not succeeded:
                # The session itself is dead (login/checkpoint redirect or
                # LinkedIn no longer serving pages).  Suspend the account —
                # same policy as SessionFailureException — instead of burning
                # through more leads against a broken session.
                if result and result.get("session_stale"):
                    account.status = LinkedInAccountStatus.SUSPENDED
                    account.updated_at = datetime.now(timezone.utc)
                    lead.status = LeadStatus.FAILED
                    db.commit()
                    logger.error("❌ Session unusable during %s: %s", _action_label(step.step_type), error_message)
                    return {"status": "failed", "reason": "session_failure", "error": error_message}

                # Transient blank page while the session is healthy — the same
                # step can succeed later, so retry it a bounded number of
                # times instead of failing the lead permanently.
                if result and result.get("page_load_failed"):
                    prior_failures = db.query(CampaignJob).filter(
                        CampaignJob.lead_id == lead_id,
                        CampaignJob.campaign_id == campaign_id,
                        CampaignJob.step_type == step.step_type.value,
                        CampaignJob.status == JobStatus.FAILED,
                    ).count()
                    if prior_failures < MAX_PAGE_LOAD_RETRIES:
                        eta = datetime.now(timezone.utc) + timedelta(
                            hours=random.uniform(*PAGE_LOAD_RETRY_DELAY_HOURS)
                        )
                        lead.next_action_at = eta
                        lead.last_action_at = datetime.now(timezone.utc)
                        job.action_message = f"{action_message} Retrying automatically."
                        db.commit()
                        celery_app.send_task(
                            "tasks.execute_campaign_step",
                            args=[lead_id, campaign_id, step_order],
                            eta=eta,
                        )
                        logger.info("🔁 %s — step rescheduled for %s", action_message, eta.isoformat())
                        return result

                lead.status = LeadStatus.FAILED
                lead.next_action_at = None
                lead.last_action_at = datetime.now(timezone.utc)
                db.commit()
                logger.warning("⚠️ %s", action_message)
                return result

            _update_lead_status(lead, step.step_type)
            try:
                _schedule_next_step(lead_id, campaign_id, step_order)
            except Exception as scheduling_exc:
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


@celery_app.task(name="tasks.dispatch_due_account_sessions")
def dispatch_due_account_sessions():
    """Queue immediate account sessions for actions whose DB due time has arrived.

    This task is run by Celery Beat.  It deliberately does not use a long
    ``eta``: ``Lead.next_action_at`` remains available after either Redis or a
    worker is restarted.  A running account session is already processing the
    due rows, so do not enqueue another session for that account every minute.
    """
    from sqlalchemy import or_

    now = datetime.now(timezone.utc)
    with get_sync_db() as db:
        initial_due = (
            ((Lead.current_step == None) | (Lead.current_step == 0))
            & (Lead.next_action_at == None)
        )
        scheduled_due = (
            (Lead.next_action_at != None) & (Lead.next_action_at <= now)
        )
        account_emails = [
            row[0]
            for row in db.query(Campaign.account_email).join(
                Lead, Lead.campaign_id == Campaign.id
            ).filter(
                Campaign.status == CampaignStatus.ACTIVE,
                Lead.status.in_([
                    LeadStatus.PENDING,
                    LeadStatus.VISITING,
                    LeadStatus.REQUESTED,
                    LeadStatus.ACCEPTED,
                    LeadStatus.MESSAGED,
                    LeadStatus.REPLIED,
                ]),
                or_(initial_due, scheduled_due),
            ).distinct().all()
        ]

    # Avoid filling the queue with one task per Beat tick while a long browser
    # session is still running.  The lock in ``run_account_session`` remains
    # the final race-safe guard; this check simply prevents the expected
    # duplicate "session already running" tasks in the normal case.
    import redis
    redis_client = redis.from_url(settings.REDIS_URL)
    dispatched = 0
    for account_email in account_emails:
        if redis_client.exists(f"session_lock:{account_email}"):
            logger.debug("⏭️ Session already active for %s; no duplicate dispatch", account_email)
            continue
        celery_app.send_task("tasks.run_account_session", args=[account_email])
        dispatched += 1

    if dispatched:
        logger.info("📅 Dispatched due account sessions for %d account(s)", dispatched)
    return {"accounts_dispatched": dispatched}


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
            
            # Mark for immediate processing by the next account session
            try:
                lead.next_action_at = datetime.now(timezone.utc)
                lead.current_step = 1
                db.commit()
                requeued_count += 1
                logger.info(f"🔄 Reset stalled lead {lead.id} for processing in next session")
            except Exception as e:
                logger.error(f"❌ Failed to reset lead {lead.id}: {e}")
                db.rollback()
        
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


def _rate_limit_for_step(step_type, campaign) -> tuple[str | None, int | None]:
    """Map a campaign step to its rate-limit action and per-campaign cap.

    ``check_and_increment()`` applies the warm-up stage caps and HARD_CAPS on
    top of the campaign limit, so passing ``None`` as the cap still bounds
    the action.
    """
    if step_type == CampaignStepType.VISIT_PROFILE:
        return "visit_profile", campaign.daily_visit_limit
    if step_type == CampaignStepType.VISIT_AND_LIKE:
        # Counted as a profile visit, matching the legacy step1 task.
        return "visit_profile", campaign.daily_visit_limit
    if step_type == CampaignStepType.LIKE_POST:
        # No per-campaign like cap exists; hard caps + warm-up caps apply.
        return "like_post", None
    if step_type == CampaignStepType.SEND_CONNECTION:
        return "send_connection", campaign.daily_connection_limit
    if step_type in (
        CampaignStepType.SEND_MESSAGE,
        CampaignStepType.FOLLOW_UP_IF_PENDING,
        CampaignStepType.THANKS_IF_ACCEPTED,
    ):
        return "send_message", campaign.daily_message_limit
    return None, None


def _action_label(step_type) -> str:
    """Stable, non-technical labels used in the UI activity feed."""
    labels = {
        CampaignStepType.VISIT_PROFILE: "Profile visit",
        CampaignStepType.LIKE_POST: "Post like",
        CampaignStepType.VISIT_AND_LIKE: "Profile visit and post like",
        CampaignStepType.SEND_CONNECTION: "Connection request",
        CampaignStepType.SEND_MESSAGE: "Message",
        CampaignStepType.FOLLOW_UP_IF_PENDING: "Follow-up message",
        CampaignStepType.THANKS_IF_ACCEPTED: "Thank-you message",
    }
    return labels.get(step_type, str(step_type))


def _step_condition_is_met(
    condition: str | None,
    lead,
    *,
    has_prior_connection_step: bool = False,
) -> bool:
    """Return whether a campaign step may run for the current lead state.

    Conditional steps are evaluated in the account-session path as well as in
    the legacy per-step path.  In particular, a message step that follows a
    connection request must not open LinkedIn until the request is accepted.
    A message step without a preceding connection step is a direct-message
    campaign for existing first-degree connections, so an old campaign that
    was saved with the UI's former default ``accepted`` condition remains
    runnable.
    """
    if not condition:
        return True
    if condition == "accepted" and not has_prior_connection_step:
        return True

    status = lead.status.value if hasattr(lead.status, "value") else lead.status
    if condition == "accepted":
        return status == LeadStatus.ACCEPTED.value
    if condition == "not_accepted":
        return status != LeadStatus.ACCEPTED.value

    # Keep older/custom campaigns runnable if they contain an unknown
    # condition.  The condition is still visible in logs for correction.
    logger.warning("⚠️ Unknown campaign step condition %r; treating it as unconditional", condition)
    return True


def _action_outcome(step_type, result: dict) -> tuple[bool, str, str | None]:
    """Turn browser results into truthful, screen-ready action feedback."""
    error = result.get("error")

    # Benign skips (lead has no posts / everything is already liked) are not
    # failures: the step can never succeed on a retry, so surface the reason
    # and let the sequence move on.
    if result.get("skipped") and not result.get("liked_post"):
        if step_type == CampaignStepType.VISIT_AND_LIKE and not result.get("visited"):
            return False, f"{_action_label(step_type)} failed: {error}", error
        return True, f"No post to like — {error}", None

    # "Already connected" or "invitation already pending" is not a failure —
    # the desired end state is already reached.  Treat it as success so the
    # campaign can continue to the next step.
    if result.get("already_connected"):
        return True, f"{_action_label(step_type)} skipped: {error}", None

    if error:
        return False, f"{_action_label(step_type)} failed: {error}", error

    if step_type == CampaignStepType.VISIT_PROFILE:
        success = bool(result.get("visited"))
        return success, ("Profile visited successfully." if success else "Profile visit was not completed."), None
    if step_type == CampaignStepType.LIKE_POST:
        success = bool(result.get("liked_post"))
        return success, ("Post liked successfully." if success else "Post was not liked; LinkedIn did not confirm the action."), None
    if step_type == CampaignStepType.VISIT_AND_LIKE:
        visited, liked = bool(result.get("visited")), bool(result.get("liked_post"))
        if visited and liked:
            return True, "Profile visited and post liked successfully.", None
        if visited:
            return False, "Profile was visited, but LinkedIn did not confirm the post like.", None
        return False, "Profile visit and post like were not completed.", None

    success = bool(result.get("sent"))
    return success, (
        f"{_action_label(step_type)} sent successfully." if success
        else f"{_action_label(step_type)} was not sent; LinkedIn did not confirm the action."
    ), None


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
 
        # Rate limit check (warm-up aware: new accounts get lower ceilings)
        if not check_and_increment(account.owner_email, "visit_profile",
                                    campaign.daily_visit_limit,
                                    warmup_stage=warmup_stage_for_account(account)):
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

        # Per-account profile lock — the Chromium user-data-dir can only be
        # open by one process at a time. Fails fast if already held.
        lock = acquire_profile_lock(account.id)
        pw = None
        context = None
        try:
            # Open the account's durable persistent profile (session state
            # lives on disk in the profile dir — nothing to load or save).
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

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
            # No save step: Chromium already persisted any cookie changes to
            # the profile directory as a side effect of the action.
            return result
        finally:
            if context:
                await context.close()
            if pw:
                await pw.stop()
            release_profile_lock(lock)


async def _run_like(account, lead) -> dict:
    """Async wrapper for the Playwright like-only action (no profile visit)."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        # Per-account profile lock — fails fast if the profile is open elsewhere.
        lock = acquire_profile_lock(account.id)
        pw = None
        context = None
        try:
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            result = await like_recent_post(page, lead.linkedin_url)
            # No save step: the profile dir on disk already has the session.
            return result
        finally:
            if context:
                await context.close()
            if pw:
                await pw.stop()
            release_profile_lock(lock)


async def _run_visit_and_like(account, lead) -> dict:
    """Async wrapper for combined visit profile and like post action."""
    with acquire_playwright_session(timeout=300) as acquired:
        if not acquired:
            raise Exception("Could not acquire Playwright session slot - timeout")

        # Per-account profile lock — fails fast if the profile is open elsewhere.
        lock = acquire_profile_lock(account.id)
        pw = None
        context = None
        try:
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

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
            # No save step: the profile dir on disk already has the session.
            return result
        finally:
            if context:
                await context.close()
            if pw:
                await pw.stop()
            release_profile_lock(lock)


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
                                    campaign.daily_connection_limit,
                                    warmup_stage=warmup_stage_for_account(account)):
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

        # Per-account profile lock — fails fast if the profile is open elsewhere.
        lock = acquire_profile_lock(account.id)
        pw = None
        context = None
        try:
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

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
            # No save step: the profile dir on disk already has the session.
            return result
        finally:
            if context:
                await context.close()
            if pw:
                await pw.stop()
            release_profile_lock(lock)
 
 
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
                                    campaign.daily_message_limit,
                                    warmup_stage=warmup_stage_for_account(account)):
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
                                    campaign.daily_message_limit,
                                    warmup_stage=warmup_stage_for_account(account)):
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
                                    campaign.daily_message_limit,
                                    warmup_stage=warmup_stage_for_account(account)):
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

        # Per-account profile lock — fails fast if the profile is open elsewhere.
        lock = acquire_profile_lock(account.id)
        pw = None
        context = None
        try:
            pw, _, context, page = await launch_persistent_browser(account, headless=True)

            verification = await verify_session(page)
            if verification.status != LinkedInSessionStatus.VALID:
                # Raise session failure exception to suspend account and stop campaign
                raise SessionFailureException(
                    f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                    f"Account will be suspended to prevent LinkedIn bot detection."
                )
            result = await send_message(page, lead.linkedin_url, message_text, lead.first_name)
            # No save step: the profile dir on disk already has the session.
            return result
        finally:
            if context:
                await context.close()
            if pw:
                await pw.stop()
            release_profile_lock(lock)


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
            
            # Query due work from the durable database schedule.  An initial
            # lead has no next_action_at and is eligible for step 1; later
            # steps are eligible only once their persisted due time arrives.
            from sqlalchemy import or_
            now = datetime.now(timezone.utc)
            initial_step_due = (
                ((Lead.current_step == None) | (Lead.current_step == 0))
                & (CampaignStep.step_order == 1)
                & (Lead.next_action_at == None)
            )
            scheduled_step_due = (
                (Lead.current_step == CampaignStep.step_order)
                & (Lead.next_action_at != None)
                & (Lead.next_action_at <= now)
            )
            due_leads = db.query(Lead, Campaign, CampaignStep).join(
                Campaign, Lead.campaign_id == Campaign.id
            ).join(
                CampaignStep, Campaign.id == CampaignStep.campaign_id
            ).filter(
                Campaign.account_email == account_email,
                Campaign.status == CampaignStatus.ACTIVE,
                Lead.status.in_([
                    LeadStatus.PENDING,
                    LeadStatus.VISITING,
                    LeadStatus.REQUESTED,
                    LeadStatus.ACCEPTED,
                    LeadStatus.MESSAGED,
                    LeadStatus.REPLIED,
                ]),
                or_(initial_step_due, scheduled_step_due),
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
            
            # Process leads in async loop. Thread the Celery task id down so
            # the helper (which is a plain async function, not a bound task)
            # can stamp CampaignJob records with it without referencing `self`.
            results = asyncio.run(_process_leads_session(
                account, due_leads, per_action_seconds, db,
                celery_task_id=self.request.id,
            ))
            
            failed_count = sum(1 for r in results if r.get("status") == "failed")
            retried_count = sum(1 for r in results if r.get("status") == "retry_scheduled")
            rate_limited_count = sum(1 for r in results if r.get("status") == "rate_limited")
            succeeded_count = len(results) - failed_count - retried_count - rate_limited_count

            logger.info(
                f"✅ Session completed for account {account_email}. Processed {len(results)} leads "
                f"({succeeded_count} succeeded, {failed_count} failed, {retried_count} rescheduled after transient "
                f"page-load failures, {rate_limited_count} deferred by daily limits)."
            )

            return {
                "status": "completed",
                "leads_processed": len(results),
                "succeeded": succeeded_count,
                "failed": failed_count,
                "retry_scheduled": retried_count,
                "rate_limited": rate_limited_count,
                "session_duration_minutes": target_duration_minutes
            }
    
    except ProfileInUseError as exc:
        # Another code path (manual verify-session, interactive login, ...)
        # currently holds this account's browser profile. Do NOT attempt to
        # launch anyway — fail cleanly and keep the self-rescheduling chain
        # alive by re-enqueueing the next session shortly.
        logger.warning(f"⚠️ Profile busy for account {account_email}: {exc}")
        self.apply_async(args=[account_email], countdown=1800)
        logger.info(f"📅 Retrying session for {account_email} in 30 minutes (profile was in use)")
        return {"status": "skipped", "reason": "account_profile_in_use"}

    except Exception as exc:
        logger.error(f"❌ Session failed for account {account_email}: {exc}")
        raise self.retry(exc=exc, countdown=random.randint(600, 1800))

    finally:
        try:
            lock.release()
        except Exception:
            # Lock may have expired or been released already (e.g., during retry)
            pass


async def _process_leads_session(account, due_leads, per_action_seconds, db,
                                 *, celery_task_id: str | None = None):
    """
    Async function that processes leads in a single browser session.

    Opens the account's durable persistent profile (the session state lives
    on disk in the profile directory — nothing to load before launch and
    nothing to save after each action) under the per-account profile lock.
    Raises ProfileInUseError if another code path already holds the profile.
    """
    # Per-account profile lock — a Chromium user-data-dir can only be held by
    # one process at a time (SingletonLock). Fails fast with a clear error if
    # the profile is already open (e.g. a manual verify-session request).
    lock = acquire_profile_lock(account.id)

    pw = None
    context = None
    results = []

    try:
        pw, _, context, page = await launch_persistent_browser(account, headless=True)
        # Persist the fingerprint pinned at first-ever launch (no-op afterwards).
        db.commit()
        # Log the pinned fingerprint so two runs of the same account can be
        # diffed to prove stability (anti-detection acceptance check).
        logger.info(
            "🪪 Session fingerprint for account %s: ua=%s viewport=%sx%s tz=%s locale=%s cpu=%s mem=%s",
            account.id, account.user_agent, account.viewport_width, account.viewport_height,
            account.timezone_id, account.locale, account.hardware_concurrency, account.device_memory,
        )

        # Verify session once at start
        verification = await verify_session(page)
        if verification.status != LinkedInSessionStatus.VALID:
            raise SessionFailureException(
                f"LinkedIn session invalid/expired/checkpoint. Status: {verification.status.value}. "
                f"Account will be suspended to prevent LinkedIn bot detection."
            )
        logger.info("✅ Session valid, starting lead processing")
        
        for i, (lead, campaign, step) in enumerate(due_leads):
            # Capture plain-string IDs up front so the exception handler
            # never needs to touch ORM attributes (which can trigger lazy
            # loads on a broken session after a failed commit/flush).
            lead_id = lead.id
            campaign_id = campaign.id
            step_type_val = step.step_type.value
            action_label = _action_label(step.step_type)
            step_order = step.step_order

            try:
                # If lead hasn't started (current_step is NULL/0), assign to step 1
                if lead.current_step is None or lead.current_step == 0:
                    lead.current_step = 1
                    db.flush()
                
                # Ensure we're processing the correct step for this lead
                if step_order != lead.current_step:
                    logger.warning(f"⚠️ Step mismatch for lead {lead_id}: step_order={step_order}, current_step={lead.current_step}, skipping")
                    continue
                
                # Check if campaign is still active (might have been paused since task started)
                db.refresh(campaign)
                if campaign.status != CampaignStatus.ACTIVE:
                    logger.info(f"⏭️ Skipping lead {lead_id} because campaign {campaign_id} is {campaign.status}")
                    continue

                # Conditions are part of the persisted campaign definition and
                # must be enforced here too.  The account-session executor is
                # the normal execution path; checking them only in the legacy
                # scheduler would otherwise attempt a message before a
                # connection is accepted.
                prior_connection_step = None
                if step.condition == "accepted":
                    prior_connection_step = db.query(CampaignStep).filter(
                        CampaignStep.campaign_id == campaign_id,
                        CampaignStep.step_order < step_order,
                        CampaignStep.step_type == CampaignStepType.SEND_CONNECTION,
                    ).first()

                if not _step_condition_is_met(
                    step.condition,
                    lead,
                    has_prior_connection_step=prior_connection_step is not None,
                ):
                    now = datetime.now(timezone.utc)
                    condition_message = (
                        "Waiting for the lead to accept the connection before sending the message."
                        if step.condition == "accepted"
                        else "Skipped because the lead is already connected."
                    )
                    job = CampaignJob(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign_id,
                        lead_id=lead_id,
                        step_type=step_type_val,
                        status=JobStatus.SKIPPED,
                        celery_task_id=celery_task_id,
                        action_message=condition_message,
                        started_at=now,
                        completed_at=now,
                    )
                    db.add(job)

                    if step.condition == "accepted":
                        # Keep the lead on this step and poll for acceptance
                        # later.  Do not mark it failed or advance past the
                        # message permanently.
                        lead.next_action_at = now + timedelta(hours=CONDITION_RETRY_HOURS)
                    else:
                        # A not_accepted branch is no longer applicable after
                        # acceptance, so continue with the next configured
                        # step without touching LinkedIn.
                        next_step = db.query(CampaignStep).filter(
                            CampaignStep.campaign_id == campaign_id,
                            CampaignStep.step_order == step_order + 1,
                        ).first()
                        if next_step:
                            lead.current_step = next_step.step_order
                            lead.next_action_at = now + timedelta(
                                hours=max(float(next_step.delay_hours or 0), 0)
                            )
                        else:
                            lead.status = LeadStatus.COMPLETE
                            lead.completed_at = now
                            lead.next_action_at = None

                    db.commit()
                    results.append({
                        "lead_id": lead_id,
                        "status": "skipped",
                        "message": condition_message,
                    })
                    logger.info("⏭️ %s for lead %s: %s", action_label, lead_id, condition_message)
                    continue

                logger.info(f"📋 Processing lead {lead_id} (step {step_order}: {step_type_val})")

                # Enforce the daily/warm-up rate limits in this primary
                # execution path as well (previously only the legacy
                # per-step tasks called check_and_increment).  Connection
                # requests are the action LinkedIn throttles hardest; an
                # uncapped burst of invites is a common trigger for the
                # blank-page / checkpoint throttling that fails steps.
                action_key, action_limit = _rate_limit_for_step(step.step_type, campaign)
                if action_key and not check_and_increment(
                    account.owner_email, action_key, action_limit,
                    warmup_stage=warmup_stage_for_account(account),
                ):
                    now = datetime.now(timezone.utc)
                    resume_at = now + timedelta(hours=random.uniform(18, 26))
                    limit_message = (
                        f"Daily {_action_label(step.step_type).lower()} limit reached — "
                        f"rescheduled for tomorrow."
                    )
                    job = CampaignJob(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign_id,
                        lead_id=lead_id,
                        step_type=step_type_val,
                        status=JobStatus.SKIPPED,
                        celery_task_id=celery_task_id,
                        action_message=limit_message,
                        started_at=now,
                        completed_at=now,
                    )
                    db.add(job)
                    # Keep the lead on this step; the next session after the
                    # counter resets picks it up.
                    lead.next_action_at = resume_at
                    lead.last_action_at = now
                    db.commit()
                    results.append({
                        "lead_id": lead_id,
                        "status": "rate_limited",
                        "message": limit_message,
                    })
                    logger.info(
                        "⏳ Daily %s limit reached; lead %s deferred to %s",
                        action_key, lead_id, resume_at.isoformat(),
                    )
                    continue

                # Record an in-progress job *before* touching LinkedIn. This
                # gives the UI an honest live status rather than reporting an
                # action as successful merely because the browser task ended.
                job = CampaignJob(
                    id=str(uuid.uuid4()),
                    campaign_id=campaign_id,
                    lead_id=lead_id,
                    step_type=step_type_val,
                    status=JobStatus.RUNNING,
                    celery_task_id=celery_task_id,
                    action_message=f"Running {action_label}...",
                    started_at=datetime.now(timezone.utc),
                )
                db.add(job)
                db.commit()

                # Execute the appropriate action and translate its structured
                # result into a user-safe status/message.
                result = await _execute_step_action(step.step_type, account, lead, campaign, page)
                succeeded, message, error_message = _action_outcome(step.step_type, result)
                job.status = JobStatus.DONE if succeeded else JobStatus.FAILED
                job.action_message = message
                job.error_message = error_message
                job.completed_at = datetime.now(timezone.utc)

                if not succeeded:
                    # The session itself is dead (login/checkpoint redirect or
                    # LinkedIn no longer serving pages).  Persist the failed
                    # job, then stop the whole session and suspend the account
                    # — continuing would burn every remaining lead against a
                    # broken session and increase bot-detection risk.
                    if result and result.get("session_stale"):
                        db.commit()
                        raise SessionFailureException(
                            error_message or "LinkedIn session became unusable mid-session."
                        )

                    # Transient blank page while the session is healthy — keep
                    # the lead on this step and retry it later instead of
                    # failing it permanently.  Bounded so a persistently
                    # unreachable lead still stops after MAX_PAGE_LOAD_RETRIES.
                    if result and result.get("page_load_failed"):
                        prior_failures = db.query(CampaignJob).filter(
                            CampaignJob.lead_id == lead_id,
                            CampaignJob.campaign_id == campaign_id,
                            CampaignJob.step_type == step_type_val,
                            CampaignJob.status == JobStatus.FAILED,
                        ).count()
                        if prior_failures < MAX_PAGE_LOAD_RETRIES:
                            retry_at = datetime.now(timezone.utc) + timedelta(
                                hours=random.uniform(*PAGE_LOAD_RETRY_DELAY_HOURS)
                            )
                            job.action_message = f"{message} Retrying automatically."
                            lead.next_action_at = retry_at
                            lead.last_action_at = datetime.now(timezone.utc)
                            db.commit()
                            results.append({
                                "lead_id": lead_id,
                                "result": result,
                                "status": "retry_scheduled",
                                "message": message,
                            })
                            logger.info(
                                "🔁 %s for lead %s: blank page (session healthy) — retrying at %s",
                                action_label, lead_id, retry_at.isoformat(),
                            )
                            continue

                    # Do not silently advance a failed action to the next step.
                    # The activity feed now shows exactly why this lead stopped.
                    lead.status = LeadStatus.FAILED
                    lead.next_action_at = None
                    lead.last_action_at = datetime.now(timezone.utc)
                    db.commit()
                    results.append({"lead_id": lead_id, "result": result, "status": "failed", "message": message})
                    logger.warning("⚠️ %s for lead %s: %s", _action_label(step.step_type), lead_id, message)
                    continue

                # Persist the next action time.  This is the source of truth
                # for delayed steps and is safe across Celery/Redis restarts.
                _update_lead_status(lead, step.step_type)
                next_step = db.query(CampaignStep).filter(
                    CampaignStep.campaign_id == campaign_id,
                    CampaignStep.step_order == step_order + 1,
                ).first()
                now = datetime.now(timezone.utc)
                lead.last_action_at = now
                if next_step:
                    lead.current_step = next_step.step_order
                    lead.next_action_at = now + timedelta(
                        hours=max(float(next_step.delay_hours or 0), 0)
                    )
                else:
                    lead.status = LeadStatus.COMPLETE
                    lead.completed_at = now
                    lead.next_action_at = None
                
                db.commit()
                results.append({"lead_id": lead_id, "result": result})
                logger.info("✅ %s for lead %s: %s", action_label, lead_id, message)
                if next_step:
                    logger.info(
                        "📅 Scheduled step %s (%s) for lead %s at %s",
                        next_step.step_order,
                        _action_label(next_step.step_type),
                        lead_id,
                        lead.next_action_at.isoformat(),
                    )

                # No explicit "save session" step: Chromium already persisted
                # any cookie/storage changes to the profile dir on disk as a
                # side effect of the action.

                # Interstitial action and dwell before next lead (except last)
                if i < len(due_leads) - 1:
                    await _interstitial_pause(page, per_action_seconds)
            
            except SessionFailureException as exc:
                # Session failure - suspend account and stop session.
                # Rollback first so the session is usable for the commit below.
                db.rollback()
                try:
                    account.status = LinkedInAccountStatus.SUSPENDED
                    account.updated_at = datetime.now(timezone.utc)
                    lead.status = LeadStatus.FAILED
                    db.commit()
                except Exception:
                    db.rollback()
                logger.error(f"❌ Session failure, suspending account: {exc}")
                raise
            
            except Exception as exc:
                # Individual lead failure - log and continue.
                # IMPORTANT: Roll back the broken transaction BEFORE touching
                # any ORM attributes or issuing new statements.
                db.rollback()
                logger.error(f"❌ Failed to process lead {lead_id}: {exc}")
                results.append({"lead_id": lead_id, "status": "failed", "error": str(exc)})
                try:
                    job = CampaignJob(
                        id=str(uuid.uuid4()),
                        campaign_id=campaign_id,
                        lead_id=lead_id,
                        step_type=step_type_val,
                        status=JobStatus.FAILED,
                        action_message=f"{action_label} failed unexpectedly.",
                        error_message=str(exc),
                        celery_task_id=celery_task_id,
                        started_at=datetime.now(timezone.utc),
                        completed_at=datetime.now(timezone.utc)
                    )
                    db.add(job)
                    db.commit()
                except Exception as db_exc:
                    logger.error(f"❌ Failed to record failed job for lead {lead_id}: {db_exc}")
                    db.rollback()
                continue

    finally:
        # Persistent context: close the context (there is no separate Browser
        # object) and stop the Playwright driver, then free the profile lock.
        if context:
            await context.close()
        if pw:
            await pw.stop()
        release_profile_lock(lock)

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

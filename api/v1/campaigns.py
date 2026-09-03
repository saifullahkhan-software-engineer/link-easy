"""
Campaign CRUD endpoints.
FILE: api/v1/campaigns.py
 
POST   /api/v1/campaigns              — create campaign
GET    /api/v1/campaigns              — list user's campaigns
GET    /api/v1/campaigns/{id}         — get single campaign
PATCH  /api/v1/campaigns/{id}         — update campaign
DELETE /api/v1/campaigns/{id}         — delete campaign
POST   /api/v1/campaigns/{id}/start   — start campaign (enqueue Celery tasks)
POST   /api/v1/campaigns/{id}/pause   — pause campaign

Lead intake (same `leads` table as CSV upload / manual entry):
POST   /api/v1/campaigns/{id}/leads/quick-add           — add one profile
POST   /api/v1/campaigns/{id}/leads/import-feed-leads   — import saved Feed Leads
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
 
from api.dependencies import get_current_user, get_db
from api.v1.linkedin import require_linkedin_enabled
from core.config import settings
from models.campaign import Campaign, CampaignStatus
from models.lead import Lead, LeadSource, LeadStatus
from models.user import User
from models.campaign_job import CampaignJob
from schemas.campaign import CampaignCreate, CampaignResponse, CampaignUpdate, CampaignStepCreate, CampaignStepUpdate, CampaignStepResponse
from schemas.feed_lead import (
    FeedLeadImportRequest,
    FeedLeadImportResponse,
    FeedLeadImportSkipped,
)
from schemas.lead import LeadQuickAdd, LeadResponse, validate_lead_fields
from models.campaign import CampaignStep
router = APIRouter(prefix="/api/v1/campaigns", tags=["campaigns"])
 
 
@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    payload: CampaignCreate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> CampaignResponse:
    # Verify the LinkedIn account belongs to the owner
    from models.linkedin_account import LinkedInAccount
    account_result = await db.execute(
        select(LinkedInAccount).where(
            LinkedInAccount.linkedin_email == payload.account_email,
            LinkedInAccount.owner_email == owner_email
        )
    )
    account = account_result.scalars().first()
    if not account and not settings.is_deployment:
        raise HTTPException(status_code=400, detail="LinkedIn account not found or does not belong to you")

    # The hosted demo cannot create real LinkedIn accounts. Keep campaigns and
    # leads usable as a demo artifact, then give a clear error when the user
    # tries to start one. The owner is encoded in the sentinel so the normal
    # campaign/lead ownership checks still work without creating a fake account.
    campaign_account_email = (
        f"demo:{owner_email}"
        if settings.is_deployment and not account
        else payload.account_email
    )
    campaign = Campaign(
        id=str(uuid.uuid4()),
        account_email=campaign_account_email,
        name=payload.name,
        description=payload.description,
        search_filters=payload.search_filters,
        daily_connection_limit=payload.daily_connection_limit or 15,
        daily_message_limit=payload.daily_message_limit or 20,
        daily_visit_limit=payload.daily_visit_limit or 80,
        connection_note_template=payload.connection_note_template,
        message_templates=payload.message_templates,
        status=CampaignStatus.DRAFT,
    )
    db.add(campaign)
    await db.flush()  # Get campaign ID before creating steps
    
    # Create campaign steps if provided
    if payload.steps:
        # Validate step orders are unique
        step_orders = [step.step_order for step in payload.steps]
        if len(step_orders) != len(set(step_orders)):
            raise HTTPException(status_code=400, detail="Step orders must be unique")
        
        for step_data in payload.steps:
            step = CampaignStep(
                id=str(uuid.uuid4()),
                campaign_id=campaign.id,
                step_order=step_data.step_order,
                step_type=step_data.step_type,
                delay_hours=step_data.delay_hours,
                condition=step_data.condition,
            )
            db.add(step)
    
    await db.commit()
    await db.refresh(campaign)
    return CampaignResponse.model_validate(campaign)
 
 
@router.get("", response_model=list[CampaignResponse])
async def list_campaigns(
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> list[CampaignResponse]:
    from models.linkedin_account import LinkedInAccount
    if settings.is_deployment:
        result = await db.execute(select(Campaign).where(Campaign.account_email == f"demo:{owner_email}"))
    else:
        result = await db.execute(select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(LinkedInAccount.owner_email == owner_email))
    campaigns = result.scalars().all()
    return [CampaignResponse.model_validate(c) for c in campaigns]
 
 
@router.get("/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> CampaignResponse:
    from models.linkedin_account import LinkedInAccount
    if settings.is_deployment:
        result = await db.execute(select(Campaign).where(
            Campaign.id == campaign_id, Campaign.account_email == f"demo:{owner_email}"))
    else:
        result = await db.execute(select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email))
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return CampaignResponse.model_validate(campaign)
 
 
@router.post(
    "/{campaign_id}/start",
    status_code=200,
    dependencies=[Depends(require_linkedin_enabled)],
)
async def start_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """Start a campaign, or explain that the hosted demo has no LinkedIn account."""
    from datetime import datetime, timezone
    import redis
    from worker.celery_app import celery_app
    from core.config import settings
    from models.linkedin_account import LinkedInAccount

    if settings.is_deployment:
        result = await db.execute(select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.account_email == f"demo:{owner_email}",
        ))
    else:
        result = await db.execute(select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email))
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.account_email.startswith("demo:"):
        raise HTTPException(status_code=400, detail=
            "No LinkedIn account is connected. Connect a LinkedIn account before starting this campaign.")
    if campaign.status == CampaignStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Campaign is already running")

    # Check if a session is already running for this account
    redis_client = redis.from_url(settings.REDIS_URL)
    lock_key = f"session_lock:{campaign.account_email}"
    if redis_client.exists(lock_key):
        raise HTTPException(status_code=409, detail="A session is already running for this account")

    # Get the first step from campaign_steps
    from models.campaign import CampaignStep
    first_step_result = await db.execute(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == 1
        )
    )
    first_step = first_step_result.scalars().first()
    
    if not first_step:
        raise HTTPException(status_code=400, detail="Campaign has no steps configured")

    # Make the first step due immediately. Human-like pacing happens inside the
    # account session; scheduling it here as a long ETA was the source of stale
    # messages that kept running after a campaign was removed.
    scheduled_at = datetime.now(timezone.utc)
    leads_result = await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.status == LeadStatus.PENDING,
            Lead.last_action_at.is_(None),
        )
    )
    for lead in leads_result.scalars().all():
        lead.current_step = first_step.step_order
        lead.next_action_at = scheduled_at

    # Commit the active status before publishing the task.  A fast worker can
    # receive a countdown task before the request handler returns; publishing
    # first used to make that worker see a draft campaign and silently skip it.
    campaign.status = CampaignStatus.ACTIVE
    campaign.started_at = datetime.now(timezone.utc)
    await db.commit()

    # Enqueue a single account session task instead of one message per lead.
    # The database timestamps remain the source of truth and Beat will recover
    # the work after a worker/Redis restart.
    celery_app.send_task(
        "tasks.run_account_session",
        args=[campaign.account_email],
    )

    return {"message": f"Campaign started. Account session queued for {campaign.account_email}.", "account_email": campaign.account_email}


@router.post("/{campaign_id}/pause", status_code=200)
async def pause_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """Pauses an active campaign and cancels pending tasks."""
    from models.linkedin_account import LinkedInAccount
    import redis
    from core.config import settings

    result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status != CampaignStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Campaign is not running")
    
    campaign.status = CampaignStatus.PAUSED
    await db.commit()
    try:
        redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
        ).delete(f"linkeasy:scheduler:account:{campaign.account_email}")
    except Exception:
        pass

    return {"message": "Campaign paused"}


@router.post(
    "/{campaign_id}/restart",
    status_code=200,
    dependencies=[Depends(require_linkedin_enabled)],
)
async def restart_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """
    Restarts a paused or failed campaign.
    Resets failed leads back to pending and enqueues tasks.
    """
    from datetime import datetime, timezone
    from worker.celery_app import celery_app
    from models.linkedin_account import LinkedInAccount
    from models.campaign import CampaignStep
    
    result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    if campaign.status == CampaignStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="Campaign is already running")
    
    # Reset only failed and skipped leads back to pending
    # Keep leads that are already in progress (VISITING, REQUESTED, etc.) or completed
    leads_result = await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.status.in_(["failed", "skipped"])
        )
    )
    failed_leads = leads_result.scalars().all()
    
    # Reset failed/skipped leads to pending and step 0
    for lead in failed_leads:
        lead.status = "pending"
        lead.current_step = 0
    
    # Get leads that are pending (new or reset) and need to start from step 1
    pending_leads_result = await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.status == "pending",
            Lead.current_step == 0
        )
    )
    pending_leads = pending_leads_result.scalars().all()
    
    # Get leads that are in progress and need to continue from their current step
    in_progress_leads_result = await db.execute(
        select(Lead).where(
            Lead.campaign_id == campaign_id,
            Lead.status.in_(["visiting", "requested", "accepted", "messaged", "replied"]),
            Lead.current_step > 0
        )
    )
    in_progress_leads = in_progress_leads_result.scalars().all()
    
    # Get the first step from campaign_steps
    first_step_result = await db.execute(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == 1
        )
    )
    first_step = first_step_result.scalars().first()
    
    if not first_step:
        raise HTTPException(status_code=400, detail="Campaign has no steps configured")
    
    # The account-session dispatcher uses current_step + next_action_at as the
    # durable schedule. Do not enqueue the retired per-lead task here: it can
    # race the account session and was the source of "step not found" skips.
    now = datetime.now(timezone.utc)
    for lead in pending_leads:
        lead.current_step = first_step.step_order
        lead.next_action_at = now
    for lead in in_progress_leads:
        lead.next_action_at = now

    campaign.status = CampaignStatus.ACTIVE
    campaign.started_at = now
    await db.commit()

    # Start promptly; Beat will dispatch all subsequent due actions.  No ETA
    # message is used, so a later pause/delete cannot leave a 5-second task in
    # the broker.
    celery_app.send_task("tasks.run_account_session", args=[campaign.account_email])

    return {
        "message": f"Campaign restarted. {len(pending_leads)} lead(s) reset and {len(in_progress_leads)} lead(s) resumed.",
        "new_leads_queued": len(pending_leads),
        "in_progress_leads": len(in_progress_leads),
    }


@router.delete("/{campaign_id}", status_code=200)
async def delete_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """
    Permanently deletes a campaign and all associated data:
    - Revokes all pending Celery tasks from Redis
    - Removes the scheduler lease from Redis
    - Deletes all campaign jobs (audit log)
    - Deletes all leads
    - Deletes all campaign steps
    - Deletes the campaign itself
    """
    import redis
    from worker.celery_app import celery_app
    from core.config import settings
    from models.linkedin_account import LinkedInAccount
    from sqlalchemy import delete as sa_delete

    # 1. Verify campaign exists and belongs to owner
    result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    account_email = campaign.account_email

    # 2. Collect all Celery task IDs from campaign_jobs before deleting
    jobs_result = await db.execute(
        select(CampaignJob.celery_task_id).where(
            CampaignJob.campaign_id == campaign_id,
            CampaignJob.celery_task_id.isnot(None),
        )
    )
    task_ids = [row[0] for row in jobs_result.all() if row[0]]

    # 3. Revoke all Celery tasks via Redis so workers discard them
    redis_client = redis.from_url(settings.REDIS_URL)
    revoked_count = 0
    for task_id in task_ids:
        try:
            celery_app.control.revoke(task_id, terminate=False)
            revoked_count += 1
        except Exception:
            pass  # Best-effort; task may already be completed or expired

    # 4. Drop only the scheduler lease. Never force-delete the live session
    # lock: another active campaign may share this LinkedIn account, and an
    # in-flight browser task must release its own lock safely.
    dispatch_lease_removed = bool(
        redis_client.delete(f"linkeasy:scheduler:account:{account_email}")
    )

    # 5. Delete campaign jobs
    await db.execute(
        sa_delete(CampaignJob).where(CampaignJob.campaign_id == campaign_id)
    )

    # 6. Delete leads (cascade from campaign)
    await db.execute(
        sa_delete(Lead).where(Lead.campaign_id == campaign_id)
    )

    # 7. Delete campaign steps (cascade from campaign)
    await db.execute(
        sa_delete(CampaignStep).where(CampaignStep.campaign_id == campaign_id)
    )

    # 8. Delete the campaign itself
    await db.execute(
        sa_delete(Campaign).where(Campaign.id == campaign_id)
    )

    await db.commit()

    return {
        "message": f"Campaign '{campaign_id}' deleted successfully.",
        "campaign_id": campaign_id,
        "tasks_revoked": revoked_count,
        "scheduler_lease_removed": dispatch_lease_removed,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Campaign Steps Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{campaign_id}/steps", response_model=list[CampaignStepResponse])
async def list_campaign_steps(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> list[CampaignStepResponse]:
    """List all steps for a campaign."""
    from models.linkedin_account import LinkedInAccount
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignStep).where(CampaignStep.campaign_id == campaign_id).order_by(CampaignStep.step_order)
    )
    steps = result.scalars().all()
    return [CampaignStepResponse.model_validate(s) for s in steps]


@router.get("/{campaign_id}/steps/{step_order}", response_model=CampaignStepResponse)
async def get_campaign_step(
    campaign_id: str,
    step_order: int,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> CampaignStepResponse:
    """Get a specific campaign step."""
    from models.linkedin_account import LinkedInAccount
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == step_order
        )
    )
    step = result.scalars().first()
    if not step:
        raise HTTPException(status_code=404, detail="Campaign step not found")
    
    return CampaignStepResponse.model_validate(step)


@router.patch("/{campaign_id}/steps/{step_order}", response_model=CampaignStepResponse)
async def update_campaign_step(
    campaign_id: str,
    step_order: int,
    payload: CampaignStepUpdate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> CampaignStepResponse:
    """Update a campaign step."""
    from models.linkedin_account import LinkedInAccount
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == step_order
        )
    )
    step = result.scalars().first()
    if not step:
        raise HTTPException(status_code=404, detail="Campaign step not found")
    
    # Update step fields
    if payload.step_type is not None:
        step.step_type = payload.step_type
    if payload.delay_hours is not None:
        step.delay_hours = payload.delay_hours
    if payload.condition is not None:
        step.condition = payload.condition
    
    await db.commit()
    await db.refresh(step)
    return CampaignStepResponse.model_validate(step)


@router.delete("/{campaign_id}/steps/{step_order}", status_code=204)
async def delete_campaign_step(
    campaign_id: str,
    step_order: int,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """Delete a campaign step."""
    from models.linkedin_account import LinkedInAccount
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignStep).where(
            CampaignStep.campaign_id == campaign_id,
            CampaignStep.step_order == step_order
        )
    )
    step = result.scalars().first()
    if not step:
        raise HTTPException(status_code=404, detail="Campaign step not found")
    
    await db.delete(step)
    await db.commit()
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# Campaign Jobs Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

@router.get("/{campaign_id}/jobs", response_model=list[dict])
async def list_campaign_jobs(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> list[dict]:
    """List all jobs for a campaign."""
    from models.linkedin_account import LinkedInAccount
    from models.campaign_job import CampaignJob
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignJob).where(CampaignJob.campaign_id == campaign_id).order_by(CampaignJob.created_at.desc())
    )
    jobs = result.scalars().all()
    
    return [
        {
            "id": job.id,
            "campaign_id": job.campaign_id,
            "lead_id": job.lead_id,
            "step_type": job.step_type,
            "celery_task_id": job.celery_task_id,
            "status": job.status.value if hasattr(job.status, 'value') else job.status,
            "action_message": job.action_message,
            "error_message": job.error_message,
            "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job in jobs
    ]


@router.get("/{campaign_id}/jobs/{job_id}", response_model=dict)
async def get_campaign_job(
    campaign_id: str,
    job_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> dict:
    """Get a specific campaign job."""
    from models.linkedin_account import LinkedInAccount
    from models.campaign_job import CampaignJob
    
    # Verify campaign belongs to owner
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    
    result = await db.execute(
        select(CampaignJob).where(
            CampaignJob.campaign_id == campaign_id,
            CampaignJob.id == job_id
        )
    )
    job = result.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="Campaign job not found")
    
    return {
        "id": job.id,
        "campaign_id": job.campaign_id,
        "lead_id": job.lead_id,
        "step_type": job.step_type,
        "celery_task_id": job.celery_task_id,
        "status": job.status.value if hasattr(job.status, 'value') else job.status,
        "action_message": job.action_message,
        "error_message": job.error_message,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Lead intake endpoints (quick-add + Feed Leads import)
#
# Both write the same `leads` table used by CSV upload and the manual form —
# there is no parallel lead pathway.  They differ only in where the profile
# came from, which is recorded on the lead's `source` fields.
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_owned_campaign(campaign_id: str, owner_email: str, db: AsyncSession) -> Campaign:
    """Fetch a campaign and verify it belongs to the owner's LinkedIn account."""
    from models.linkedin_account import LinkedInAccount

    if settings.is_deployment:
        result = await db.execute(select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.account_email == f"demo:{owner_email}",
        ))
    else:
        result = await db.execute(select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email))
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found or does not belong to you")
    return campaign


@router.post(
    "/{campaign_id}/leads/quick-add",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a single profile to this campaign's leads",
)
async def quick_add_lead(
    campaign_id: str,
    payload: LeadQuickAdd,
    db: AsyncSession = Depends(get_db),
) -> LeadResponse:
    """
    Adds one profile as a campaign lead in a single call.

    Same validation as CSV import (first_name, last_name and a valid
    ``/in/`` LinkedIn URL are required; headline optional).  If the profile is
    already a lead of this campaign the call fails with **409** — never a
    silent no-op — so the caller can switch its button to the "Added" state.
    """
    from api.v1.leads import build_lead, find_duplicate_lead

    campaign = await _get_owned_campaign(campaign_id, payload.owner_email, db)

    try:
        cleaned = validate_lead_fields(payload.first_name, payload.last_name, payload.linkedin_url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    duplicate = await find_duplicate_lead(db, campaign.id, cleaned["linkedin_url"])
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"Already in {campaign.name} leads",
                "code": "already_in_campaign",
                "lead_id": duplicate.id,
                "campaign_id": campaign.id,
                "campaign_name": campaign.name,
            },
        )

    lead = build_lead(
        campaign_id=campaign.id,
        first_name=cleaned["first_name"],
        last_name=cleaned["last_name"],
        linkedin_url=cleaned["linkedin_url"],
        headline=(payload.headline or "").strip() or None,
        source=payload.source,
        source_post_url=payload.source_post_url,
        matched_score=payload.matched_score,
        matched_criteria=payload.matched_criteria,
        scan_id=payload.scan_id,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    return LeadResponse.model_validate(lead)


@router.post(
    "/{campaign_id}/leads/import-feed-leads",
    response_model=FeedLeadImportResponse,
    summary="Import selected Feed Leads into this campaign",
)
async def import_feed_leads(
    campaign_id: str,
    payload: FeedLeadImportRequest,
    db: AsyncSession = Depends(get_db),
) -> FeedLeadImportResponse:
    """
    Turns saved Feed Leads (profiles staged from Feed Scroll scan results) into
    real campaign leads.

    Behaviour per selected entry:

    * **added** — inserted into the shared ``leads`` table with
      ``status=pending`` and the feed scan metadata (source, post URL, score,
      matched criteria, scan id) preserved; the pool entry is consumed.
    * **duplicate** — this LinkedIn URL is already a lead of this campaign, so
      nothing is inserted; the pool entry is still consumed and pointed at the
      existing lead, and the entry is reported back so the UI can say so.
    * **error** — validation failed or the entry is gone; it stays in the pool.
    """
    from api.v1.leads import build_lead, find_duplicate_lead
    from models.feed_lead import FeedLead, FeedLeadStatus

    campaign = await _get_owned_campaign(campaign_id, payload.owner_email, db)

    requested_ids = list(dict.fromkeys(payload.feed_lead_ids))  # de-dupe, keep order
    result = await db.execute(
        select(FeedLead).where(
            FeedLead.id.in_(requested_ids),
            FeedLead.owner_email == payload.owner_email,
        )
    )
    found = {row.id: row for row in result.scalars().all()}

    added: list[Lead] = []
    duplicates: list[FeedLeadImportSkipped] = []
    errors: list[FeedLeadImportSkipped] = []
    imported_at = datetime.now(timezone.utc)
    # Guards against the same profile appearing twice inside one selection.
    seen_urls: set[str] = set()

    for feed_lead_id in requested_ids:
        feed_lead = found.get(feed_lead_id)
        if feed_lead is None:
            errors.append(FeedLeadImportSkipped(
                feed_lead_id=feed_lead_id,
                reason="not_found",
                message="This feed lead no longer exists.",
            ))
            continue

        display_name = " ".join(
            part for part in [feed_lead.first_name, feed_lead.last_name] if part
        ) or feed_lead.linkedin_url

        if feed_lead.status == FeedLeadStatus.IMPORTED:
            duplicates.append(FeedLeadImportSkipped(
                feed_lead_id=feed_lead.id,
                linkedin_url=feed_lead.linkedin_url,
                name=display_name,
                reason="duplicate",
                message="Already imported into a campaign.",
            ))
            continue

        try:
            cleaned = validate_lead_fields(
                feed_lead.first_name, feed_lead.last_name, feed_lead.linkedin_url
            )
        except ValueError as exc:
            errors.append(FeedLeadImportSkipped(
                feed_lead_id=feed_lead.id,
                linkedin_url=feed_lead.linkedin_url,
                name=display_name,
                reason="invalid",
                message=str(exc),
            ))
            continue

        existing = await find_duplicate_lead(db, campaign.id, cleaned["linkedin_url"])
        if existing is not None or cleaned["linkedin_url"] in seen_urls:
            # Consume the pool entry: the profile is in the campaign already.
            feed_lead.status = FeedLeadStatus.IMPORTED
            feed_lead.imported_campaign_id = campaign.id
            feed_lead.imported_lead_id = existing.id if existing else None
            feed_lead.imported_at = imported_at
            duplicates.append(FeedLeadImportSkipped(
                feed_lead_id=feed_lead.id,
                linkedin_url=cleaned["linkedin_url"],
                name=display_name,
                reason="duplicate",
                message=f"Already in {campaign.name} leads",
            ))
            continue

        lead = build_lead(
            campaign_id=campaign.id,
            first_name=cleaned["first_name"],
            last_name=cleaned["last_name"],
            linkedin_url=cleaned["linkedin_url"],
            headline=feed_lead.headline,
            source=LeadSource.JOB_FEED_SCAN,
            source_post_url=feed_lead.source_post_url,
            matched_score=feed_lead.matched_score,
            matched_criteria=feed_lead.matched_criteria,
            scan_id=feed_lead.scan_id,
        )
        db.add(lead)

        feed_lead.status = FeedLeadStatus.IMPORTED
        feed_lead.imported_campaign_id = campaign.id
        feed_lead.imported_lead_id = lead.id
        feed_lead.imported_at = imported_at

        seen_urls.add(cleaned["linkedin_url"])
        added.append(lead)

    await db.commit()
    for lead in added:
        await db.refresh(lead)

    return FeedLeadImportResponse(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        added=[LeadResponse.model_validate(lead) for lead in added],
        duplicates=duplicates,
        errors=errors,
    )

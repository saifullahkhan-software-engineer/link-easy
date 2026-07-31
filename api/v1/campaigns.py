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
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
 
from api.dependencies import get_current_user, get_db
from models.campaign import Campaign, CampaignStatus
from models.lead import Lead, LeadStatus
from models.user import User
from models.campaign_job import CampaignJob
from schemas.campaign import CampaignCreate, CampaignResponse, CampaignUpdate, CampaignStepCreate, CampaignStepUpdate, CampaignStepResponse
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
    if not account:
        raise HTTPException(status_code=400, detail="LinkedIn account not found or does not belong to you")

    campaign = Campaign(
        id=str(uuid.uuid4()),
        account_email=payload.account_email,
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
    result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(LinkedInAccount.owner_email == owner_email)
    )
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
    result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return CampaignResponse.model_validate(campaign)
 
 
@router.post("/{campaign_id}/start", status_code=200)
async def start_campaign(
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
):
    """
    Sets campaign to ACTIVE and enqueues Step 1 Celery tasks for all pending leads.
    Tasks are spread across a random time window minimum 2-hour window to avoid burst detection.
    """
    from datetime import datetime, timedelta, timezone
    import random
    import redis
    from worker.celery_app import celery_app
    from core.config import settings
    from models.linkedin_account import LinkedInAccount

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

    # Persist the initial schedule before returning so the UI can immediately
    # show both the next step and its execution time. Use the same delay for the
    # Celery task; next_action_at remains the durable source of truth.
    session_delay_seconds = random.randint(30, 120)
    scheduled_at = datetime.now(timezone.utc) + timedelta(seconds=session_delay_seconds)
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

    # Enqueue a single account session task instead of per-lead tasks.
    celery_app.send_task(
        "tasks.run_account_session",
        args=[campaign.account_email],
        countdown=session_delay_seconds,
    )

    campaign.status = CampaignStatus.ACTIVE
    campaign.started_at = datetime.now(timezone.utc)
    await db.commit()

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
    from worker.celery_app import celery_app
    
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
    
    return {"message": "Campaign paused"}


@router.post("/{campaign_id}/restart", status_code=200)
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
    import random
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
    
    # Enqueue pending leads for step 1
    for i, lead in enumerate(pending_leads):
        base_delay = random.randint(30, 120)
        position_delay = i * random.randint(30, 60)
        delay_seconds = base_delay + position_delay
        celery_app.send_task(
            "tasks.execute_campaign_step",
            args=[lead.id, campaign_id, first_step.step_order],
            countdown=delay_seconds,
        )
    
    # Enqueue in-progress leads for their next step (current_step + 1)
    for i, lead in enumerate(in_progress_leads):
        next_step_order = lead.current_step + 1
        base_delay = random.randint(30, 120)
        position_delay = i * random.randint(30, 60)
        delay_seconds = base_delay + position_delay
        celery_app.send_task(
            "tasks.execute_campaign_step",
            args=[lead.id, campaign_id, next_step_order],
            countdown=delay_seconds,
        )
    
    campaign.status = CampaignStatus.ACTIVE
    campaign.started_at = datetime.now(timezone.utc)
    await db.commit()
    
    return {"message": f"Campaign restarted. {len(pending_leads)} new leads queued, {len(in_progress_leads)} in-progress leads continuing.", "new_leads_queued": len(pending_leads), "in_progress_leads": len(in_progress_leads)}


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
    - Removes the session lock from Redis
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

    # 4. Remove the session lock from Redis (if present for this account)
    session_lock_key = f"session_lock:{account_email}"
    lock_removed = bool(redis_client.delete(session_lock_key))

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
        "session_lock_removed": lock_removed,
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
        "error_message": job.error_message,
        "scheduled_at": job.scheduled_at.isoformat() if job.scheduled_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }

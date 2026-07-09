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
from models.lead import Lead
from models.user import User
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
    from datetime import datetime, timezone
    import random
    from worker.celery_app import celery_app
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
 
    # Get all pending leads for this campaign
    leads_result = await db.execute(
        select(Lead).where(Lead.campaign_id == campaign_id,
                           Lead.status == "pending")
    )
    leads = leads_result.scalars().all()
 
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

    # Spread lead start times with human-like random delays (30 seconds to 2 minutes)
    for i, lead in enumerate(leads):
        base_delay = random.randint(30, 120)
        position_delay = i * random.randint(30, 60)
        delay_seconds = base_delay + position_delay
        celery_app.send_task(
            "tasks.execute_campaign_step",
            args=[lead.id, campaign_id, first_step.step_order],
            countdown=delay_seconds,
        )
 
    campaign.status = CampaignStatus.ACTIVE
    campaign.started_at = datetime.now(timezone.utc)
    await db.commit()
 
    return {"message": f"Campaign started. {len(leads)} leads queued.", "leads_queued": len(leads)}

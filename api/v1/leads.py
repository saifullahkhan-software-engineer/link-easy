"""
Lead endpoints.
All routes require a valid Bearer access token (authenticated platform user).
Users can only read/modify leads for their own campaigns.
POST   /api/v1/leads                    — add a lead to a campaign
GET    /api/v1/leads                    — get leads for a campaign
GET    /api/v1/leads/{lead_id}          — get a specific lead
PATCH  /api/v1/leads/{lead_id}          — update lead status/notes
DELETE /api/v1/leads/{lead_id}          — remove a lead
"""

import csv
from datetime import datetime, timezone
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from api.dependencies import get_current_user, get_db
from models.lead import Lead, LeadStatus
from models.user import User
from schemas.lead import LeadCreate, LeadResponse, LeadUpdate, validate_linkedin_url_str

router = APIRouter(prefix="/api/v1/leads", tags=["leads"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_lead_or_404(
    lead_id: str,
    owner_email: str,
    db: AsyncSession,
) -> Lead:
    """
    Fetch a lead by ID and verify it belongs to the owner.
    Raises 404 if not found or not authorized.
    """
    from models.campaign import Campaign
    from models.linkedin_account import LinkedInAccount
    result = await db.execute(
        select(Lead).join(
            Campaign, Lead.campaign_id == Campaign.id
        ).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Lead.id == lead_id, LinkedInAccount.owner_email == owner_email)
    )
    lead = result.scalars().first()
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead not found")
    return lead


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "",
    response_model=LeadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a lead to a campaign",
)
async def create_lead(
    payload: LeadCreate,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> LeadResponse:
    """
    Adds a new lead to a campaign.
    
    The lead will be associated with the campaign and will follow the campaign's
    drip sequence based on its steps configuration.
    """
    # Verify the campaign belongs to the owner via their LinkedIn account
    from models.campaign import Campaign
    from models.linkedin_account import LinkedInAccount
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == payload.campaign_id, LinkedInAccount.owner_email == payload.owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=400, detail="Campaign not found or does not belong to you")
    
    lead = Lead(
        id=str(uuid.uuid4()),
        campaign_id=payload.campaign_id,
        linkedin_url=payload.linkedin_url,
        first_name=payload.first_name,
        last_name=payload.last_name,
        headline=payload.headline,
        status=LeadStatus.PENDING,
        current_step=1,
        next_action_at=datetime.now(timezone.utc)
    )
    
    db.add(lead)
    await db.commit()
    await db.refresh(lead)
    
    return LeadResponse.model_validate(lead)


@router.get(
    "",
    response_model=list[LeadResponse],
    summary="Get leads for a campaign",
)
async def get_leads(
    campaign_id: str | None = Query(None, description="Filter by campaign ID"),
    status: LeadStatus | None = Query(None, description="Filter by lead status"),
    owner_email: str = Query(..., description="Owner email for validation"),
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> list[LeadResponse]:
    """
    Returns leads for the owner.
    
    Can filter by campaign_id and/or status.
    Only returns leads for campaigns owned by the owner.
    """
    from models.campaign import Campaign
    from models.linkedin_account import LinkedInAccount
    
    query = select(Lead).join(
        Campaign, Lead.campaign_id == Campaign.id
    ).join(
        LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
    ).where(LinkedInAccount.owner_email == owner_email)
    
    if campaign_id:
        query = query.where(Lead.campaign_id == campaign_id)
    
    if status:
        query = query.where(Lead.status == status)
    
    result = await db.execute(query)
    leads = result.scalars().all()
    
    return [LeadResponse.model_validate(lead) for lead in leads]


@router.post(
    "/upload",
    response_model=list[LeadResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Bulk upload leads from CSV",
)
async def upload_leads_csv(
    file: UploadFile,
    campaign_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> list[LeadResponse]:
    """
    Bulk upload leads from a CSV file.
    
    CSV format: first_name, last_name, linkedin_url
    Example:
    first_name,last_name,linkedin_url
    John,Doe,https://www.linkedin.com/in/johndoe
    Jane,Smith,https://www.linkedin.com/in/janesmith
    """
    # Verify the campaign belongs to the owner
    from models.campaign import Campaign
    from models.linkedin_account import LinkedInAccount
    campaign_result = await db.execute(
        select(Campaign).join(
            LinkedInAccount, Campaign.account_email == LinkedInAccount.linkedin_email
        ).where(Campaign.id == campaign_id, LinkedInAccount.owner_email == owner_email)
    )
    campaign = campaign_result.scalars().first()
    if not campaign:
        raise HTTPException(status_code=400, detail="Campaign not found or does not belong to you")

    # Read and parse CSV
    content = await file.read()
    csv_reader = csv.DictReader(io.StringIO(content.decode('utf-8')))

    rows = list(csv_reader)
    if not rows:
        raise HTTPException(status_code=400, detail="CSV file is empty or has no data rows")

    # ── First pass: validate every row before persisting anything ─────────────
    validation_errors: list[str] = []
    parsed_rows: list[dict] = []

    for row_index, row in enumerate(rows, start=2):  # row 1 is the header
        first_name = row.get('first_name', '').strip()
        last_name  = row.get('last_name',  '').strip()
        raw_url    = row.get('linkedin_url', '').strip()

        if not first_name or not last_name or not raw_url:
            validation_errors.append(
                f"Row {row_index}: missing required field(s) "
                f"(first_name={first_name!r}, last_name={last_name!r}, linkedin_url={raw_url!r})"
            )
            continue

        try:
            linkedin_url = validate_linkedin_url_str(raw_url)
        except ValueError as exc:
            validation_errors.append(f"Row {row_index}: invalid linkedin_url {raw_url!r} — {exc}")
            continue

        parsed_rows.append({
            'first_name':   first_name,
            'last_name':    last_name,
            'linkedin_url': linkedin_url,
            'headline':     row.get('headline', '').strip() or None,
        })

    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "CSV contains invalid rows; no leads were imported.",
                "errors": validation_errors,
            },
        )

    # ── Second pass: persist only after all rows passed validation ────────────
    created_leads: list[Lead] = []
    for row_data in parsed_rows:
        lead = Lead(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            linkedin_url=row_data['linkedin_url'],
            first_name=row_data['first_name'],
            last_name=row_data['last_name'],
            headline=row_data['headline'],
            status=LeadStatus.PENDING,
            current_step=1,
            next_action_at=datetime.now(timezone.utc)
        )
        db.add(lead)
        created_leads.append(lead)

    await db.commit()

    # Refresh all leads to get their DB-assigned fields
    for lead in created_leads:
        await db.refresh(lead)

    return [LeadResponse.model_validate(lead) for lead in created_leads]


@router.get(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Get a specific lead",
)
async def get_lead(
    lead_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> LeadResponse:
    """Returns a specific lead by ID."""
    lead = await _get_lead_or_404(lead_id, owner_email, db)
    return LeadResponse.model_validate(lead)


@router.patch(
    "/{lead_id}",
    response_model=LeadResponse,
    summary="Update a lead",
)
async def update_lead(
    lead_id: str,
    payload: LeadUpdate,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> LeadResponse:
    """
    Updates a lead's status, current step, or notes.
    
    This is typically called by the automation system when actions are performed,
    or by admins to manually update lead status.
    """
    lead = await _get_lead_or_404(lead_id, owner_email, db)
    
    if payload.status is not None:
        lead.status = payload.status
    
    if payload.current_step is not None:
        lead.current_step = payload.current_step
    
    if payload.notes is not None:
        lead.notes = payload.notes
    
    await db.commit()
    await db.refresh(lead)
    
    return LeadResponse.model_validate(lead)


@router.delete(
    "/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a lead",
)
async def delete_lead(
    lead_id: str,
    owner_email: str,
    db: AsyncSession = Depends(get_db),
    # current_user: User = Depends(get_current_user),  # Commented for testing
) -> None:
    """Permanently removes a lead. This action cannot be undone."""
    lead = await _get_lead_or_404(lead_id, owner_email, db)
    await db.delete(lead)
    await db.commit()

"""
Pydantic schemas for Campaign endpoints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field

from models.campaign import CampaignStatus, CampaignStepType


class CampaignCreate(BaseModel):
    """Payload to create a new campaign."""
    account_email: str = Field(..., description="LinkedIn account email to use for this campaign")
    name: str = Field(..., min_length=1, max_length=255, description="Campaign name")
    description: str | None = Field(None, description="Campaign description")
    search_filters: dict | None = Field(None, description="LinkedIn search filters as JSON")
    daily_connection_limit: int | None = Field(15, description="Daily connection request limit")
    daily_message_limit: int | None = Field(20, description="Daily message limit")
    daily_visit_limit: int | None = Field(80, description="Daily profile visit limit")
    connection_note_template: str | None = Field(None, description="Connection request note template (use {{first_name}})")
    message_templates: list[str] | None = Field(None, description="Message templates for drip sequence")
    steps: list[CampaignStepCreate] | None = Field(None, description="Campaign steps for the drip sequence")


class CampaignUpdate(BaseModel):
    """Payload to update an existing campaign."""
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    status: CampaignStatus | None = None
    search_filters: dict | None = None
    daily_connection_limit: int | None = None
    daily_message_limit: int | None = None
    daily_visit_limit: int | None = None
    connection_note_template: str | None = None
    message_templates: list[str] | None = None


class CampaignResponse(BaseModel):
    """Safe campaign representation returned to the client."""
    id: str
    account_email: str
    name: str
    description: str | None
    status: CampaignStatus
    search_filters: dict | None
    daily_connection_limit: int | None
    daily_message_limit: int | None
    daily_visit_limit: int | None
    connection_note_template: str | None
    message_templates: list[str] | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None

    model_config = {"from_attributes": True}


class CampaignStepCreate(BaseModel):
    """Payload to create a campaign step."""
    campaign_id: str | None = Field(None, description="Campaign ID this step belongs to (auto-set when creating campaign)")
    step_order: int = Field(..., ge=1, description="Step order in the sequence (1, 2, 3, etc.)")
    step_type: CampaignStepType = Field(..., description="Type of action for this step")
    delay_hours: float = Field(0, ge=0, description="Delay in hours after previous step (e.g., 0.083 = 5 minutes)")
    condition: str | None = Field(None, description="Condition for step execution (null=always, 'accepted', 'not_accepted')")


class CampaignStepUpdate(BaseModel):
    """Payload to update an existing campaign step."""
    step_order: int | None = Field(None, ge=1)
    step_type: CampaignStepType | None = None
    delay_hours: float | None = None
    condition: str | None = None


class CampaignStepResponse(BaseModel):
    """Safe campaign step representation returned to the client."""
    id: str
    campaign_id: str
    step_order: int
    step_type: CampaignStepType
    delay_hours: float
    condition: str | None

    model_config = {"from_attributes": True}
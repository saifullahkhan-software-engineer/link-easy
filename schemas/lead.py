"""
Pydantic schemas for Lead endpoints.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_core.core_schema import FieldValidationInfo

from models.lead import LeadStatus


def validate_linkedin_url_str(v: str) -> str:
    """
    Validate that *v* is a properly-formed LinkedIn profile URL.

    Rules:
    - Must start with ``https://www.linkedin.com/in/``
    - Trailing slash is stripped for normalisation

    Raises ``ValueError`` on failure so it can be used both as a Pydantic
    field validator and as a standalone guard in non-Pydantic code paths
    (e.g. CSV bulk upload).
    """
    v = v.strip()
    if not v.startswith('https://www.linkedin.com/in/'):
        raise ValueError('LinkedIn URL must start with https://www.linkedin.com/in/')
    # Normalise: strip trailing slash
    return v.rstrip('/')


class LeadCreate(BaseModel):
    """Payload to create a new lead."""
    owner_email: str = Field(..., description="Owner email for validation")
    campaign_id: str = Field(..., description="Campaign ID this lead belongs to")
    linkedin_url: str = Field(..., description="Full LinkedIn profile URL")
    first_name: str | None = Field(None, description="Lead's first name")
    last_name: str | None = Field(None, description="Lead's last name")
    headline: str | None = Field(None, description="LinkedIn headline")

    @field_validator('linkedin_url')
    @classmethod
    def validate_linkedin_url(cls, v: str) -> str:
        """Validate that the URL is a LinkedIn profile URL."""
        return validate_linkedin_url_str(v)



class LeadUpdate(BaseModel):
    """Payload to update an existing lead."""
    status: LeadStatus | None = Field(None, description="Update lead status")
    current_step: int | None = Field(None, description="Current step in drip sequence")
    notes: str | None = Field(None, description="Admin or debug notes")


class LeadResponse(BaseModel):
    """Safe lead representation returned to the client."""
    id: str
    campaign_id: str
    linkedin_url: str
    first_name: str | None
    last_name: str | None
    headline: str | None
    status: LeadStatus
    current_step: int | None
    connection_sent_at: datetime | None
    accepted_at: datetime | None
    last_action_at: datetime | None
    next_action_at: datetime | None
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}

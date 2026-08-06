"""
Pydantic schemas for Lead endpoints.
"""

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, Field, field_validator
from pydantic_core.core_schema import FieldValidationInfo

from models.lead import LeadSource, LeadStatus


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


def validate_lead_fields(
    first_name: str | None,
    last_name: str | None,
    linkedin_url: str | None,
) -> dict[str, str]:
    """
    Single source of truth for lead identity validation.

    Used by every pathway that creates a lead — CSV bulk import, the manual
    "Add manually" form, the Feed Leads pool and the campaign quick-add — so
    they can never drift apart:

    - ``first_name``, ``last_name`` and ``linkedin_url`` are required
    - ``linkedin_url`` must be a personal profile URL (``/in/<slug>``) and is
      normalised (trimmed, trailing slash removed)

    Returns the cleaned values; raises ``ValueError`` with a human-readable
    message on the first problem found.
    """
    first = (first_name or '').strip()
    last = (last_name or '').strip()
    raw_url = (linkedin_url or '').strip()

    missing = [
        name
        for name, value in (
            ('first_name', first),
            ('last_name', last),
            ('linkedin_url', raw_url),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"missing required field(s): {', '.join(missing)} "
            f"(first_name={first!r}, last_name={last!r}, linkedin_url={raw_url!r})"
        )

    try:
        normalised_url = validate_linkedin_url_str(raw_url)
    except ValueError as exc:
        raise ValueError(f"invalid linkedin_url {raw_url!r} — {exc}") from exc

    return {
        'first_name': first,
        'last_name': last,
        'linkedin_url': normalised_url,
    }


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



class LeadQuickAdd(BaseModel):
    """
    Payload for ``POST /api/v1/campaigns/{campaign_id}/leads/quick-add``.

    Same required fields and validation as CSV import (first_name, last_name,
    valid LinkedIn profile URL); ``headline`` is optional.  The remaining
    fields are source metadata stored for analytics and never shown as part of
    the lead itself.
    """
    owner_email: str = Field(..., description="Owner email for validation")
    first_name: str = Field(..., description="Lead's first name")
    last_name: str = Field(..., description="Lead's last name")
    linkedin_url: str = Field(..., description="Full LinkedIn profile URL")
    headline: str | None = Field(None, description="LinkedIn headline (optional)")

    source: LeadSource = Field(LeadSource.JOB_FEED_SCAN, description="How the lead was captured")
    source_post_url: str | None = Field(None, description="Post the profile was matched on")
    matched_score: float | None = Field(None, description="Relevance score of the matched post")
    matched_criteria: list[str] | None = Field(None, description="Criteria that matched the post")
    scan_id: str | None = Field(None, description="Feed scan batch the post came from")


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
    time_remaining_ms: int | None = None

    # Provenance — surfaced in Manage Leads so a user can tell a Feed Scroll
    # match from a CSV import or a manual entry.
    source: str | None = None
    source_post_url: str | None = None
    matched_score: float | None = None
    matched_criteria: list[str] | None = None
    scan_id: str | None = None

    model_config = {"from_attributes": True}

    @classmethod
    def model_validate(cls, obj, **kwargs):
        instance = super().model_validate(obj, **kwargs)
        if hasattr(obj, 'next_action_at') and obj.next_action_at and obj.status not in [LeadStatus.COMPLETE, LeadStatus.FAILED]:
            now = datetime.now(timezone.utc)
            # Ensure next_action_at is timezone aware for comparison
            next_at = obj.next_action_at
            if next_at.tzinfo is None:
                next_at = next_at.replace(tzinfo=timezone.utc)
            diff = next_at - now
            instance.time_remaining_ms = max(0, int(diff.total_seconds() * 1000))
        return instance

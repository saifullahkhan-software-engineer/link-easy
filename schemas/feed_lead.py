"""
Pydantic schemas for the Feed Leads pool.

The pool is the staging area between a Feed Scroll scan result and a campaign:
saving a post's author creates a ``FeedLead`` (per feed-scroll job), and the
campaign "Feed Leads" tab turns a selection of them into real campaign leads
using the same validation as CSV/manual import.
"""
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from models.feed_lead import FEED_LEAD_SOURCE, FeedLeadStatus
from schemas.lead import LeadResponse, validate_linkedin_url_str


class FeedLeadCreate(BaseModel):
    """Payload to save a scanned profile into a feed-scroll job's pool."""
    owner_email: str = Field(..., description="Owner email for validation")
    feed_scroll_job_id: str = Field(..., description="Pool (feed scroll job) to save into")
    feed_scroll_result_id: str | None = Field(None, description="Scored post row the profile came from")

    first_name: str = Field(..., description="Parsed from the poster's display name")
    last_name: str = Field(..., description="Parsed from the poster's display name")
    linkedin_url: str = Field(..., description="Verified profile link shown on the card")
    headline: str | None = Field(None, description="Optional, same as CSV import")

    label: str | None = Field(None, max_length=120, description="Optional user label for this saved lead")

    # Source metadata — not shown to the user, stored for analytics.
    source: str = Field(FEED_LEAD_SOURCE, description="Always job_feed_scan for this pathway")
    source_post_url: str | None = None
    matched_score: float | None = None
    matched_criteria: list[str] | None = None
    scan_id: str | None = None

    @field_validator("linkedin_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        return validate_linkedin_url_str(v)

    @field_validator("first_name", "last_name")
    @classmethod
    def _validate_names(cls, v: str) -> str:
        cleaned = (v or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned


class FeedLeadResponse(BaseModel):
    """A single saved profile in the pool."""
    id: str
    owner_email: str
    feed_scroll_job_id: str
    feed_scroll_result_id: str | None
    linkedin_url: str
    first_name: str | None
    last_name: str | None
    headline: str | None
    label: str | None
    source: str
    source_post_url: str | None
    matched_score: float | None
    matched_criteria: list[str] | None
    scan_id: str | None
    status: FeedLeadStatus
    imported_campaign_id: str | None
    imported_lead_id: str | None
    imported_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedLeadPoolResponse(BaseModel):
    """A pool = one feed scroll job plus how many profiles are waiting in it."""
    feed_scroll_job_id: str
    name: str
    mode: str | None = None
    status: str | None = None
    saved_count: int = 0
    imported_count: int = 0
    last_saved_at: datetime | None = None


class FeedLeadImportRequest(BaseModel):
    """Payload to turn selected pool entries into campaign leads."""
    owner_email: str = Field(..., description="Owner email for validation")
    feed_lead_ids: list[str] = Field(..., min_length=1, description="Pool entries to import")


class FeedLeadImportSkipped(BaseModel):
    """One pool entry that could not be imported, and why."""
    feed_lead_id: str
    linkedin_url: str | None = None
    name: str | None = None
    reason: str  # "duplicate" | "invalid" | "not_found"
    message: str


class FeedLeadImportResponse(BaseModel):
    """Result of a bulk import: what landed, what was already there, what failed."""
    campaign_id: str
    campaign_name: str
    added: list[LeadResponse] = []
    duplicates: list[FeedLeadImportSkipped] = []
    errors: list[FeedLeadImportSkipped] = []

    @property
    def added_count(self) -> int:  # pragma: no cover - convenience only
        return len(self.added)

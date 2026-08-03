"""
Pydantic schemas for Feed Scroll endpoints.
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from models.feed_scroll_job import FeedScrollMode, FeedScrollJobStatus


class FeedScrollJobCreate(BaseModel):
    """Payload to create a new feed scroll job."""
    account_email: str = Field(..., description="LinkedIn account email")
    owner_email: str = Field(..., description="Owner user email")
    name: str = Field(..., min_length=1, max_length=255)
    mode: FeedScrollMode = Field(..., description="job_search or post_search")

    # Job Search criteria
    experience_min_years: Optional[int] = Field(None, ge=0)
    experience_max_years: Optional[int] = Field(None, ge=0)
    job_titles: Optional[list[str]] = None
    skill_set: Optional[list[str]] = None

    # Post Search criteria
    keywords: Optional[list[str]] = None

    # Scheduling
    feed_interval_hours: int = Field(1, ge=1, le=24)
    posts_per_scan: int = Field(10, ge=1, le=50)


class FeedScrollJobUpdate(BaseModel):
    """Payload to update a feed scroll job."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    status: Optional[FeedScrollJobStatus] = None
    experience_min_years: Optional[int] = None
    experience_max_years: Optional[int] = None
    job_titles: Optional[list[str]] = None
    skill_set: Optional[list[str]] = None
    keywords: Optional[list[str]] = None
    feed_interval_hours: Optional[int] = Field(None, ge=1, le=24)
    posts_per_scan: Optional[int] = Field(None, ge=1, le=50)


class FeedScrollJobResponse(BaseModel):
    """Feed scroll job returned to the client."""
    id: str
    account_email: str
    owner_email: str
    name: str
    mode: FeedScrollMode
    status: FeedScrollJobStatus
    experience_min_years: Optional[int]
    experience_max_years: Optional[int]
    job_titles: Optional[list[str]]
    skill_set: Optional[list[str]]
    keywords: Optional[list[str]]
    feed_interval_hours: int
    posts_per_scan: int
    last_scanned_at: Optional[datetime]
    next_scan_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FeedScrollResultResponse(BaseModel):
    """Single scored post returned to the client."""
    id: str
    feed_scroll_job_id: str
    post_urn: Optional[str]
    post_url: Optional[str]
    author_name: Optional[str]
    post_text: Optional[str]
    score: float
    matched_terms: Optional[list[str]]
    scan_batch_id: str
    scanned_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}

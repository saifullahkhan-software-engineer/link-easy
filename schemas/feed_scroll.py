"""
Pydantic schemas for Feed Scroll endpoints.
"""
import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from models.feed_scroll_job import (
    DEFAULT_POSTS_PER_SCAN,
    MAX_POSTS_PER_SCAN,
    FeedScrollMode,
    FeedScrollJobStatus,
)


def normalize_tags(v: Any) -> Optional[list[str]]:
    """Split comma/semicolon/newline-separated strings into a list of trimmed unique strings.

    ``None`` is preserved so callers can distinguish "field not provided"
    (leave unchanged) from an explicit empty list (clear the field).
    """
    if v is None:
        return None
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return None

    result = []
    for item in v:
        if not item:
            continue
        parts = re.split(r"[,;\n]+", str(item))
        for part in parts:
            cleaned = part.strip()
            if cleaned and cleaned not in result:
                result.append(cleaned)
    return result


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
    # Keywords strengthen a job search as a separate, weighted signal.  They
    # remain the sole criterion for post_search mode.
    keywords: Optional[list[str]] = None

    # Scheduling
    feed_interval_hours: int = Field(1, ge=1, le=24)
    # Keep up to the twenty highest-scoring posts from a scan.
    posts_per_scan: int = Field(
        DEFAULT_POSTS_PER_SCAN, ge=1, le=MAX_POSTS_PER_SCAN
    )

    @field_validator("job_titles", "skill_set", "keywords", mode="before")
    @classmethod
    def clean_tag_fields(cls, v: Any) -> Optional[list[str]]:
        return normalize_tags(v)

    @model_validator(mode="after")
    def validate_search_criteria(self) -> "FeedScrollJobCreate":
        """Require at least one useful criterion in either search mode."""
        if self.mode == FeedScrollMode.JOB_SEARCH:
            if not any((self.job_titles, self.skill_set, self.keywords)):
                raise ValueError(
                    "Job search requires at least one job title, skill, or keyword"
                )
        elif not self.keywords:
            raise ValueError("Post search requires at least one keyword")

        if (
            self.experience_min_years is not None
            and self.experience_max_years is not None
            and self.experience_min_years > self.experience_max_years
        ):
            raise ValueError("Minimum experience cannot exceed maximum experience")
        return self


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
    posts_per_scan: Optional[int] = Field(None, ge=1, le=MAX_POSTS_PER_SCAN)
    remaining_seconds: Optional[int] = None

    @field_validator("job_titles", "skill_set", "keywords", mode="before")
    @classmethod
    def clean_tag_fields(cls, v: Any) -> Optional[list[str]]:
        return normalize_tags(v)


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
    remaining_seconds: Optional[int] = None
    last_scanned_at: Optional[datetime]
    next_scan_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    # Set only on criteria edits (PATCH): how many previously stored results
    # were re-scored and kept, and how many were removed because they no
    # longer match the new keywords / experience / job titles.
    rescored_results: Optional[int] = None
    removed_results: Optional[int] = None

    model_config = {"from_attributes": True}


class FeedScrollResultResponse(BaseModel):
    """Single scored post returned to the client.

    Every surfaced post is guaranteed to carry both a clickable ``post_url``
    and the author's clickable ``author_profile_url``.  Rows missing either
    URL are filtered before this response is built.
    """
    id: str
    feed_scroll_job_id: str
    post_urn: Optional[str]
    post_url: str
    author_name: Optional[str]
    author_first_name: Optional[str]
    author_last_name: Optional[str]
    author_profile_url: str
    connection_degree: Optional[str]
    post_time: Optional[str]
    post_text: Optional[str]
    score: float
    matched_terms: Optional[list[str]]
    scan_batch_id: str
    dismissed_at: Optional[datetime] = None
    is_applied: bool = False
    applied_at: Optional[datetime] = None
    scanned_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedScrollAppliedPostResponse(BaseModel):
    """Post marked as applied by the user."""
    id: str
    feed_scroll_job_id: str
    owner_email: str
    post_urn: Optional[str] = None
    post_url: str
    author_name: Optional[str] = None
    author_first_name: Optional[str] = None
    author_last_name: Optional[str] = None
    author_profile_url: str
    connection_degree: Optional[str] = None
    post_time: Optional[str] = None
    post_text: Optional[str] = None
    score: float = 0.0
    matched_terms: Optional[list[str]] = None
    scan_batch_id: Optional[str] = None
    applied_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class FeedScrollAppliedPostCreate(BaseModel):
    """Payload to mark a post as applied."""
    result_id: Optional[str] = None
    post_urn: Optional[str] = None
    post_url: str
    author_name: Optional[str] = None
    author_first_name: Optional[str] = None
    author_last_name: Optional[str] = None
    author_profile_url: str
    connection_degree: Optional[str] = None
    post_time: Optional[str] = None
    post_text: Optional[str] = None
    score: Optional[float] = 0.0
    matched_terms: Optional[list[str]] = None
    scan_batch_id: Optional[str] = None


class FeedScrollBulkDeleteRequest(BaseModel):
    """Payload to delete multiple applied posts."""
    post_ids: list[str]

"""
Social post scheduler — Pydantic schemas.
FILE: schemas/social_scheduler.py

Ported from social_scheduler/schemas/__init__.py. Changes on the way in:

* ``PostCreate`` no longer accepts ``video_path``. The worker opens that path
  and streams the file to YouTube/TikTok, so letting the client choose it was
  an arbitrary-file-read. Clients now pass the ``upload_id`` returned by the
  upload endpoint and the server resolves the path itself.
* Platform names and the status filter are validated against the enums in
  models/social_scheduler.py instead of free-form strings.
* Token-bearing fields never appear in any response schema; the connection
  response exposes account metadata only.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from models.social_scheduler import SocialPlatform, SocialPostStatus

PLATFORM_VALUES = tuple(p.value for p in SocialPlatform)
PLATFORM_LABELS = {"youtube": "YouTube Shorts", "instagram": "Instagram Reels", "tiktok": "TikTok", "facebook": "Facebook Page"}


def _validate_platforms(values: list[str]) -> list[str]:
    if not values:
        raise ValueError("Select at least one platform")
    cleaned: list[str] = []
    for raw in values:
        value = str(raw).strip().lower()
        if value not in PLATFORM_VALUES:
            raise ValueError(f"Unknown platform '{raw}'. Choose from: {', '.join(PLATFORM_VALUES)}")
        if value not in cleaned:
            cleaned.append(value)
    return cleaned


def _ensure_aware(value: datetime) -> datetime:
    # A bare "2026-09-10T18:00" from a <input type=datetime-local> is taken as
    # UTC; a value with an offset is normalised to UTC for comparisons.
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ── Posts ─────────────────────────────────────────────────────────────────────


class PostCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    caption: str = Field("", max_length=5000)
    hashtags: str = Field("", max_length=1000)
    # Returned by POST /upload — the server maps it back to the stored file.
    upload_id: str = Field(..., min_length=1, max_length=200)
    thumbnail: str = ""
    platforms: list[str]
    scheduled_at: datetime
    youtube_title: str = Field("", max_length=100)
    instagram_caption: str = Field("", max_length=2200)
    tiktok_caption: str = Field("", max_length=2200)

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        return _validate_platforms(v)

    @field_validator("scheduled_at")
    @classmethod
    def _aware(cls, v):
        return _ensure_aware(v)

    @field_validator("title", "caption", "hashtags", "youtube_title", "instagram_caption", "tiktok_caption")
    @classmethod
    def _strip(cls, v):
        return v.strip() if isinstance(v, str) else v


class PostUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    caption: Optional[str] = Field(None, max_length=5000)
    hashtags: Optional[str] = Field(None, max_length=1000)
    platforms: Optional[list[str]] = None
    scheduled_at: Optional[datetime] = None
    # Only "pending" (re-schedule a failed/cancelled post) and "cancelled"
    # may be set by the client; the worker owns posting/posted/failed.
    status: Optional[str] = None
    youtube_title: Optional[str] = Field(None, max_length=100)
    instagram_caption: Optional[str] = Field(None, max_length=2200)
    tiktok_caption: Optional[str] = Field(None, max_length=2200)

    @field_validator("platforms")
    @classmethod
    def _platforms(cls, v):
        return None if v is None else _validate_platforms(v)

    @field_validator("scheduled_at")
    @classmethod
    def _aware(cls, v):
        return None if v is None else _ensure_aware(v)

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is None:
            return None
        value = str(v).strip().lower()
        allowed = (SocialPostStatus.PENDING.value, SocialPostStatus.CANCELLED.value)
        if value not in allowed:
            raise ValueError(f"status may only be set to {' or '.join(allowed)}")
        return value


class PostResultResponse(BaseModel):
    id: str
    platform: str
    status: str
    platform_id: str = ""
    platform_url: str = ""
    error: str = ""
    posted_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PostResponse(BaseModel):
    id: str
    title: str
    caption: str
    hashtags: str
    video_url: str
    thumbnail: str
    platforms: list[str]
    scheduled_at: datetime
    status: str
    youtube_title: str
    instagram_caption: str
    tiktok_caption: str
    created_at: datetime
    updated_at: datetime
    results: list[PostResultResponse] = []

    model_config = {"from_attributes": True}


class PostDeleteResponse(BaseModel):
    message: str
    id: str


# ── Upload ────────────────────────────────────────────────────────────────────


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    content_type: str
    video_url: str


# ── Platform connections ──────────────────────────────────────────────────────


class PlatformConnectionResponse(BaseModel):
    """A connection as shown to its owner — never includes token material."""

    platform: str
    label: str
    connected: bool
    # False when the operator has not set this platform's OAuth app
    # credentials; the UI disables the connect button and says why.
    configured: bool
    account_name: str = ""
    account_id: str = ""
    expires_at: Optional[datetime] = None
    # True when the access token is past expiry and no refresh token is held,
    # so the next publish would fail — the UI asks for a reconnect.
    reconnect_required: bool = False
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PlatformAuthUrlResponse(BaseModel):
    platform: str
    auth_url: str


class PlatformDisconnectResponse(BaseModel):
    message: str
    platform: str


# ── Stats ─────────────────────────────────────────────────────────────────────


class StatsResponse(BaseModel):
    scheduled_this_week: int
    total_scheduled: int
    total_published: int
    total_failed: int
    next_post_at: Optional[datetime] = None
    # Human string such as "in 2 days 3 hours"; kept for the dashboard header.
    next_post_in: Optional[str] = None
    connected_platforms: list[str]
    # Publishes per platform over all time — feeds the history page summary.
    per_platform: dict[str, dict[str, int]] = {}


class CalendarDay(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    posts: list[PostResponse]


# ── OAuth callback (query params on the platform redirect) ────────────────────


class OAuthCallbackQuery(BaseModel):
    code: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    error_description: Optional[str] = None
    extra: dict[str, Any] = {}

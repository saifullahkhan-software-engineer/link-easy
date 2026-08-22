"""
WhatsApp Job Scanner — Pydantic schemas.
FILE: schemas/whatsapp.py
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Connection ────────────────────────────────────────────────────────────────


class WhatsAppConnectResponse(BaseModel):
    message: str
    status: str  # waiting_qr | connected | error


class WhatsAppDisconnectResponse(BaseModel):
    message: str
    status: str = "disconnected"


class WhatsAppCaptureResponse(BaseModel):
    """Result of the manual "I've scanned it — capture session" action."""

    message: str
    status: str = "connected"
    # False when the logged-in chat surface could not be detected and the
    # session was captured anyway (force). Useful for UI copy/telemetry.
    detected: bool = True
    updated_at: Optional[datetime] = None


class WhatsAppStatusResponse(BaseModel):
    status: str  # disconnected | waiting_qr | connected | error
    is_active: bool
    # Metadata for the manage-account card (mirrors LinkedInAccount): when the
    # connection was added / last updated. Null when no session row exists.
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Groups ────────────────────────────────────────────────────────────────────


class WhatsAppGroupItem(BaseModel):
    group_name: str
    whatsapp_id: Optional[str] = None


class WhatsAppGroupListResponse(BaseModel):
    groups: list[WhatsAppGroupItem]
    monitored_group_names: list[str] = Field(default_factory=list)
    forward_group_name: Optional[str] = None


class WhatsAppGroupSelectRequest(BaseModel):
    # Optional for the legacy singleton scanner. New filter jobs always send
    # their filter id so every job keeps an independent group configuration.
    filter_id: Optional[int] = None
    monitored_group_names: list[str] = Field(..., min_length=1, max_length=3)
    monitored_group_ids: list[str] = Field(..., min_length=1, max_length=3)
    forward_group_name: str = Field(..., min_length=1)
    forward_group_id: Optional[str] = None

    @model_validator(mode="after")
    def validate_group_lists(self):
        if len(self.monitored_group_names) != len(self.monitored_group_ids):
            raise ValueError("monitored group names and ids must have the same length")
        normalized_names = [name.strip().casefold() for name in self.monitored_group_names]
        if any(not name for name in normalized_names):
            raise ValueError("monitored group names cannot be empty")
        if len(set(normalized_names)) != len(normalized_names):
            raise ValueError("monitored groups must be unique")
        normalized_ids = [group_id.strip() for group_id in self.monitored_group_ids if group_id.strip()]
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("monitored group ids must be unique")
        return self


class WhatsAppGroupSelectResponse(BaseModel):
    message: str
    monitored_groups: list[str]
    forward_group: str


class WhatsAppSavedGroup(BaseModel):
    """Saved group configuration and its durable incremental-scan cursor."""

    id: int
    group_name: str
    whatsapp_id: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_message_id: Optional[str] = None
    last_message_timestamp: Optional[str] = None

    model_config = {"from_attributes": True}


class WhatsAppSavedForwardGroup(BaseModel):
    id: int
    group_name: str
    whatsapp_id: Optional[str] = None

    model_config = {"from_attributes": True}


# ── Filters ───────────────────────────────────────────────────────────────────


class WhatsAppScanFilterRequest(BaseModel):
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    experience_level: Optional[str] = None  # entry | mid | senior
    match_threshold: float = Field(60.0, ge=0.0, le=100.0)
    interval_hours: float = Field(1.0, ge=0.25, le=168.0)
    latest_messages_limit: int = Field(20, ge=1, le=100)

    @field_validator("experience_level")
    @classmethod
    def validate_experience_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("entry", "mid", "senior"):
            raise ValueError("experience_level must be one of: entry, mid, senior")
        return v


class WhatsAppScanFilterCreate(WhatsAppScanFilterRequest):
    """Payload for a new filter job.

    A filter is created as ``draft``. Groups can be configured on the separate
    edit page before the user starts the scheduler.
    """
    name: str = Field(..., min_length=1, max_length=255)


class WhatsAppScanFilterUpdate(BaseModel):
    """Partial update for an existing filter job."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    experience_level: Optional[str] = None
    match_threshold: Optional[float] = Field(None, ge=0.0, le=100.0)
    interval_hours: Optional[float] = Field(None, ge=0.25, le=168.0)
    latest_messages_limit: Optional[int] = Field(None, ge=1, le=100)

    @field_validator("experience_level")
    @classmethod
    def validate_experience_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("entry", "mid", "senior"):
            raise ValueError("experience_level must be one of: entry, mid, senior")
        return v


class WhatsAppScanFilterResponse(BaseModel):
    id: int
    name: str = "WhatsApp Filter"
    owner_email: Optional[str] = None
    status: str = "draft"  # draft | active | paused
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    experience_level: Optional[str] = None
    match_threshold: float = 60.0
    interval_hours: float = 1.0
    latest_messages_limit: int = 20
    remaining_seconds: Optional[int] = None
    next_scan_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    last_scan_at: Optional[datetime] = None
    monitored_group_names: list[str] = Field(default_factory=list)
    monitored_groups: list[WhatsAppSavedGroup] = Field(default_factory=list)
    forward_group_name: Optional[str] = None
    forward_group: Optional[WhatsAppSavedForwardGroup] = None
    total_count: int = 0
    matched_count: int = 0
    rejected_count: int = 0
    forwarded_count: int = 0

    model_config = {"from_attributes": True}


# ── Messages ──────────────────────────────────────────────────────────────────


class WhatsAppMessageResponse(BaseModel):
    id: int
    filter_id: Optional[int] = None
    group_id: int
    sender_name: Optional[str] = None
    message_text: Optional[str] = None
    ocr_text: Optional[str] = None
    message_type: str
    match_score: Optional[float] = None
    status: str
    forwarded: bool
    forwarded_at: Optional[datetime] = None
    ocr_failed: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class WhatsAppMessageListResponse(BaseModel):
    messages: list[WhatsAppMessageResponse]
    total: int
    page: int
    page_size: int


# ── Stats ─────────────────────────────────────────────────────────────────────


class WhatsAppStatsResponse(BaseModel):
    matched_count: int
    rejected_count: int
    forwarded_count: int
    pending_count: int
    total_count: int

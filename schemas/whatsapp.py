"""
WhatsApp Job Scanner — Pydantic schemas.
FILE: schemas/whatsapp.py
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Connection ────────────────────────────────────────────────────────────────


class WhatsAppConnectResponse(BaseModel):
    message: str
    status: str  # waiting_qr | connected | error


class WhatsAppStatusResponse(BaseModel):
    status: str  # disconnected | waiting_qr | connected | error
    is_active: bool


# ── Groups ────────────────────────────────────────────────────────────────────


class WhatsAppGroupItem(BaseModel):
    group_name: str
    whatsapp_id: Optional[str] = None


class WhatsAppGroupListResponse(BaseModel):
    groups: list[WhatsAppGroupItem]
    monitored_group_names: list[str] = []
    forward_group_name: Optional[str] = None


class WhatsAppGroupSelectRequest(BaseModel):
    monitored_group_names: list[str] = Field(..., min_length=3, max_length=3)
    monitored_group_ids: list[str] = Field(..., min_length=3, max_length=3)
    forward_group_name: str
    forward_group_id: Optional[str] = None


class WhatsAppGroupSelectResponse(BaseModel):
    message: str
    monitored_groups: list[str]
    forward_group: str


# ── Filters ───────────────────────────────────────────────────────────────────


class WhatsAppScanFilterRequest(BaseModel):
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    experience_level: Optional[str] = None  # entry | mid | senior
    match_threshold: float = Field(60.0, ge=0.0, le=100.0)
    interval_hours: float = Field(1.0, ge=0.25, le=168.0)

    @field_validator("experience_level")
    @classmethod
    def validate_experience_level(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("entry", "mid", "senior"):
            raise ValueError("experience_level must be one of: entry, mid, senior")
        return v


class WhatsAppScanFilterResponse(BaseModel):
    id: int
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    experience_level: Optional[str] = None
    match_threshold: float = 60.0
    interval_hours: float = 1.0
    updated_at: Optional[datetime] = None
    last_scan_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Messages ──────────────────────────────────────────────────────────────────


class WhatsAppMessageResponse(BaseModel):
    id: int
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

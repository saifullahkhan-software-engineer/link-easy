"""
Admin dashboard schemas.

FILE: schemas/admin.py
"""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, EmailStr, Field


# ── Users ────────────────────────────────────────────────────────────────────


class AdminUserRow(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    is_verified: bool
    roles: list[str]
    primary_role: str
    linkedin_accounts: int = 0
    campaigns: int = 0
    created_at: Optional[datetime] = None


class AdminUsersResponse(BaseModel):
    users: list[AdminUserRow]
    count: int


class UpdateUserRolesRequest(BaseModel):
    roles: list[str] = Field(
        ...,
        min_length=1,
        description="Full set of roles for the user, e.g. ['admin', 'customer']",
    )


class UpdateUserRolesResponse(BaseModel):
    email: EmailStr
    roles: list[str]
    primary_role: str


# ── Me / role discovery ──────────────────────────────────────────────────────


class MyRolesResponse(BaseModel):
    email: EmailStr
    roles: list[str]
    is_admin: bool
    # False while ADMIN_API_ENFORCED is off (bootstrap mode).
    admin_api_enforced: bool


# ── Settings ─────────────────────────────────────────────────────────────────


class SettingRow(BaseModel):
    key: str
    value: Any
    default: Any
    value_type: str
    category: str
    description: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None


class SettingsResponse(BaseModel):
    settings: list[SettingRow]


class UpdateSettingsRequest(BaseModel):
    values: dict[str, Any] = Field(..., description="key -> new value")


# ── Overview ─────────────────────────────────────────────────────────────────


class AdminOverviewResponse(BaseModel):
    users: dict[str, Any]
    accounts: dict[str, Any]
    jobs: dict[str, Any]
    rate_limits: dict[str, Any]
    generated_at: datetime


# ── Accounts (LinkedIn + WhatsApp sessions) ──────────────────────────────────


class AdminLinkedInAccountRow(BaseModel):
    id: str
    owner_email: Optional[str] = None
    linkedin_email: str
    label: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Pure fact: the durable Chromium profile dir for this account is missing
    # or empty. Meaningful when the status says the account is usable — a
    # "connected" account with a missing profile was wiped (volume not
    # mounted) and its next session launch lands on a blank login.
    profile_missing: bool = False


class AdminWhatsAppSessionRow(BaseModel):
    id: int
    status: str
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Per-user rollout: which platform user owns this session (NULL = legacy
    # unowned row).
    owner_email: Optional[str] = None
    # Same fact as the LinkedIn rows, now computed per session profile.
    profile_missing: bool = False


class AdminAccountsResponse(BaseModel):
    linkedin: list[AdminLinkedInAccountRow]
    whatsapp: list[AdminWhatsAppSessionRow]
    counts: dict[str, Any] = Field(default_factory=dict)


# ── LinkedIn jobs (campaign job audit log) ───────────────────────────────────


class AdminLinkedInJobRow(BaseModel):
    id: str
    campaign_id: str
    campaign_name: Optional[str] = None
    step_type: str
    status: str
    action_message: Optional[str] = None
    error_message: Optional[str] = None
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AdminLinkedInJobsResponse(BaseModel):
    jobs: list[AdminLinkedInJobRow]
    count: int


# ── WhatsApp jobs (filter jobs) ──────────────────────────────────────────────


class AdminWhatsAppJobRow(BaseModel):
    id: int
    name: str
    status: str
    role: Optional[str] = None
    job_title: Optional[str] = None
    keywords: Optional[list[str]] = None
    interval_hours: float = 1.0
    next_scan_at: Optional[datetime] = None
    last_scan_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    total_count: int = 0
    matched_count: int = 0
    rejected_count: int = 0
    forwarded_count: int = 0


class AdminWhatsAppJobsResponse(BaseModel):
    jobs: list[AdminWhatsAppJobRow]
    count: int

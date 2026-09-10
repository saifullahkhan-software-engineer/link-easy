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


# ── Closed-session cleanup (WhatsApp) ────────────────────────────────────────
#
# A "closed" session is one that can no longer do work: it is disconnected,
# errored, stuck waiting for a QR scan, or flagged inactive. Deleting it only
# ever removes the session row (plus its on-disk browser profile) — filter
# jobs attached to the session are DETACHED (session_id → NULL) and stay with
# the login account that owns them. No endpoint here deletes a filter.


class ClosedSessionsCleanupRequest(BaseModel):
    statuses: list[str] = Field(
        default_factory=lambda: ["disconnected", "error", "waiting_qr"],
        description="Session statuses treated as closed",
    )
    include_inactive: bool = Field(
        default=True,
        description="Also match rows flagged is_active=false whatever their status",
    )
    older_than_days: Optional[int] = Field(
        default=None, ge=0, description="Only match sessions idle longer than this"
    )
    limit: int = Field(default=100, ge=1, le=500)
    dry_run: bool = Field(
        default=False, description="Preview matches without deleting anything"
    )
    session_ids: Optional[list[int]] = Field(
        default=None,
        description="Restrict to these ids (only the closed ones are deleted)",
    )


class ClosedSessionItem(BaseModel):
    id: int
    status: str
    is_active: bool
    owner_email: Optional[str] = None
    updated_at: Optional[datetime] = None
    detached_filters: int = 0


class ClosedSessionsCleanupResponse(BaseModel):
    dry_run: bool
    matched: int
    deleted: int
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    sessions: list[ClosedSessionItem] = Field(default_factory=list)
    # Filters are never deleted — this is how many were detached (kept).
    preserved_filters: int = 0
    revoked_tasks: int = 0
    profile_dirs_removed: int = 0
    errors: list[str] = Field(default_factory=list)


# ── LinkedIn (campaign) job deletion ─────────────────────────────────────────


class LinkedInJobsBulkDeleteRequest(BaseModel):
    statuses: Optional[list[str]] = Field(
        default=None,
        description="Only delete these statuses (queued/running/done/failed/skipped)",
    )
    older_than_days: Optional[int] = Field(default=None, ge=0)
    campaign_id: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    dry_run: bool = False
    job_ids: Optional[list[str]] = None


class LinkedInJobsBulkDeleteResponse(BaseModel):
    dry_run: bool
    matched: int
    deleted: int
    revoked_tasks: int = 0
    sample: list[dict[str, Any]] = Field(default_factory=list)


# ── Stale Celery-task inspection / cleanup (all users) ───────────────────────
#
# Reserved/scheduled Celery tasks whose campaign, feed scan, or filter is no
# longer active are "stale": revoking them only drops transient queue state.
# No database row — job or filter — is ever deleted by this cleanup.


class StaleTaskItem(BaseModel):
    id: str
    name: str
    args: list[Any] = Field(default_factory=list)
    scope: str = "unknown"
    reason: str = ""


class StalePreviewResponse(BaseModel):
    scope: str
    inspected: int
    stale_count: int
    stale: list[StaleTaskItem] = Field(default_factory=list)
    workers: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class StaleCleanupRequest(BaseModel):
    scope: str = Field(
        default="all", description="all | linkedin | whatsapp | feed"
    )
    limit: int = Field(default=50, ge=1, le=200)
    dry_run: bool = Field(
        default=True, description="Preview only unless explicitly false"
    )


class StaleCleanupResponse(BaseModel):
    scope: str
    dry_run: bool
    inspected: int
    revoked_count: int = 0
    revoked: list[StaleTaskItem] = Field(default_factory=list)
    deleted_lease_count: int = 0
    deleted_leases: list[str] = Field(default_factory=list)
    workers: list[str] = Field(default_factory=list)
    error: Optional[str] = None

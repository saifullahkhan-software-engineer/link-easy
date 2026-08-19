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

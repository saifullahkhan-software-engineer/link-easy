"""
Pydantic schemas for LinkedIn account endpoints.

Passwords are accepted in plain form over HTTPS — the API layer is
responsible for encrypting them before they ever touch the database.
"""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, field_validator

from models.linkedin_account import LinkedInAccountStatus


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class LinkedInAccountCreate(BaseModel):
    """Payload to add a new LinkedIn account."""
    owner_email: EmailStr | None = Field(
        default=None,
        description=(
            "Ignored when a LinkEasy session is present. Ownership is always "
            "taken from the authenticated user so callers cannot attach an "
            "account to someone else."
        ),
    )

    linkedin_email: EmailStr = Field(
        ..., description="The email address used to log in to LinkedIn"
    )
    linkedin_password: Annotated[
        str,
        Field(
            ...,
            min_length=6,
            max_length=128,
            description="LinkedIn account password (transmitted over HTTPS, encrypted at rest)",
        ),
    ]
    label: Annotated[
        str | None,
        Field(
            default=None,
            max_length=64,
            description="Optional human-readable label, e.g. 'Work Account'",
        ),
    ] = None

    @field_validator("linkedin_password")
    @classmethod
    def password_not_whitespace(cls, v: str) -> str:
        if v != v.strip():
            raise ValueError("Password must not start or end with whitespace")
        return v


class LinkedInAccountCookieConnect(BaseModel):
    """Payload to connect a LinkedIn account with an imported session cookie.

    The user signs in to LinkedIn in their OWN browser and pastes the
    resulting session cookie, so the server never submits the sign-in form —
    which is what usually trips LinkedIn's CAPTCHA from a datacenter IP.
    No password is transmitted or stored on this path.
    """

    linkedin_email: EmailStr = Field(
        ...,
        description=(
            "The LinkedIn account the cookie belongs to. Used for labelling "
            "and duplicate detection only — the cookie itself is what "
            "authenticates."
        ),
    )
    session_cookie: Annotated[
        str,
        Field(
            ...,
            min_length=20,
            max_length=20_000,
            description=(
                "The li_at cookie value, a full 'name=value; ...' cookie "
                "string, or a JSON cookie export. Transmitted over HTTPS and "
                "written only into the account's browser profile."
            ),
        ),
    ]
    label: Annotated[
        str | None,
        Field(default=None, max_length=64, description="Optional human-readable label"),
    ] = None

    @field_validator("session_cookie")
    @classmethod
    def cookie_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Session cookie must not be empty")
        return v


class LinkedInAccountUpdate(BaseModel):
    """
    Payload to update an existing LinkedIn account.
    All fields are optional — only provided fields are changed.
    """

    linkedin_password: Annotated[
        str | None,
        Field(
            default=None,
            min_length=6,
            max_length=128,
            description="New LinkedIn password (leave empty to keep existing)",
        ),
    ] = None
    linkedin_email: Annotated[
        EmailStr | None,
        Field(
            default=None, description="New LinkedIn email (leave empty to keep existing)"
        ),
    ] = None
    label: Annotated[
        str | None,
        Field(default=None, max_length=64),
    ] = None

    @field_validator("linkedin_password")
    @classmethod
    def password_not_whitespace(cls, v: str | None) -> str | None:
        if v is not None and v != v.strip():
            raise ValueError("Password must not start or end with whitespace")
        return v


# ---------------------------------------------------------------------------
# Output schemas — NEVER expose encrypted_password to the client
# ---------------------------------------------------------------------------

class LinkedInAccountResponse(BaseModel):
    """Safe account representation returned to the client."""

    owner_email: EmailStr
    linkedin_email: EmailStr
    label: str | None
    status: LinkedInAccountStatus
    # "password" or "cookie" — the UI uses this to explain that a cookie
    # account cannot be auto-relogged-in and must be re-imported when its
    # session expires.
    auth_method: str = "password"
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

class LinkedInAccountDeleteResponse(BaseModel):
    message: str


class LinkedInAccountCreateResponse(BaseModel):
    """Response after adding a LinkedIn account."""
    status: str  # "LOGIN_SUCCESS" or "PENDING_VERIFICATION"
    session_id: str | None  # Session ID if pending verification
    message: str
    account: LinkedInAccountResponse | None  # Account data if login successful


class VerificationCodeRequest(BaseModel):
    """Payload to submit verification code for pending login."""
    session_id: str = Field(..., description="Session ID from pending login")
    verification_code: str = Field(..., min_length=4, max_length=10, description="Verification code from LinkedIn")


class VerificationCodeResponse(BaseModel):
    """Response after verification code submission."""
    status: str  # "LOGIN_SUCCESS" or "VERIFICATION_FAILED"
    message: str
    account: LinkedInAccountResponse | None  # Account data if verification successful


class SessionVerificationResponse(BaseModel):
    """Response after LinkedIn session verification."""
    # "ACTIVE", "REFRESHED", "PENDING_VERIFICATION", "FAILED",
    # or "IN_USE" (another session currently holds the account's browser profile)
    status: str
    message: str
    account: LinkedInAccountResponse | None  # Account data if available
    requires_manual_verification: bool = False
    session_id: str | None = None  # Session ID if pending verification (for checkpoint)

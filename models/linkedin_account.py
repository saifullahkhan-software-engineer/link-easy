"""
LinkedInAccount model.

Stores one LinkedIn account per row, linked to a platform user via email (FK).
The LinkedIn password is NEVER stored in plaintext — only AES-256-GCM
encrypted ciphertext is persisted (see core.security.encrypt_credential).

Session state is NOT stored in the database. Every account owns a durable
Chromium profile directory on disk (``profile_dir``); that directory is the
source of truth for cookies / localStorage / IndexedDB and is persisted by
Chromium itself as a side effect of normal browsing.

Status lifecycle:
    pending_verification  → account added, not yet confirmed working
    active                → Playwright login confirmed, automation can run
    failed                → login failed (bad credentials / checkpoint)
    suspended             → manually disabled by admin or rate-limit breach
"""

import enum
import uuid

from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, Integer, String
from sqlalchemy.sql import func

from database import Base
from core.config import settings


class LinkedInAccountStatus(str, enum.Enum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE               = "active"
    VALID                = "valid"
    FAILED               = "failed"
    SUSPENDED            = "suspended"


class WarmupStage(str, enum.Enum):
    """
    Optional manual override for account warm-up pacing used by the rate
    limiter (worker/rate_limit.py). When NULL, the stage is derived from
    account age: <14 days → NEW, 14-27 days → RAMPING, 28+ → ESTABLISHED.
    """
    NEW         = "new"
    RAMPING     = "ramping"
    ESTABLISHED = "established"


class LinkedInAccount(Base):
    __tablename__ = "linkedin_accounts"

    # ── Surrogate server-generated primary key ────────────────────────────────
    # linkedin_email is user-controlled input and must NEVER be used to build
    # filesystem paths (path-traversal / directory-collision risk). The durable
    # profile directory is derived from this UUID — and only this UUID.
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"))
    linkedin_email     = Column(String, nullable=False, unique=True, index=True)
    encrypted_password = Column(String, nullable=False)
    label              = Column(String, nullable=True)
    status             = Column(SAEnum(LinkedInAccountStatus, name="linkedin_account_status", create_type=False),
                                nullable=False, default=LinkedInAccountStatus.PENDING_VERIFICATION)

    # ── Durable per-account Chromium profile ──────────────────────────────────
    # Set once at account-creation time as f"{PROFILE_STORAGE_DIR}/{id}".
    # All browser launches (login, verification, campaign sessions) open this
    # same user-data-dir via launch_persistent_context(); there is no explicit
    # "save session" step — Chromium persists to disk continuously.
    profile_dir = Column(String, nullable=False)

    # ── Pinned browser fingerprint (anti-detection) ───────────────────────────
    # Generated ONCE at first successful launch/login and reused unchanged on
    # every subsequent launch of this account's profile. Randomization happens
    # only BETWEEN different accounts, never within one account's lifetime.
    # Nullable until the first login pins them.
    user_agent           = Column(String, nullable=True)
    viewport_width       = Column(Integer, nullable=True)
    viewport_height      = Column(Integer, nullable=True)
    timezone_id          = Column(String, nullable=True)
    locale               = Column(String, nullable=True)
    hardware_concurrency = Column(Integer, nullable=True)
    device_memory        = Column(Integer, nullable=True)

    # ── Warm-up stage (rate-limit pacing) ─────────────────────────────────────
    # Optional manual override; NULL means "derive from account age".
    warmup_stage = Column(
        SAEnum(WarmupStage, name="linkedin_warmup_stage", native_enum=False),
        nullable=True,
    )

    # ── Webshare proxy assigned to this account ───────────────────────────────
    # STICKY: exactly one proxy is assigned per account permanently (written to
    # these columns once, at assignment time). Never rotate these per-session —
    # an account that changes IPs between sessions is a strong automation tell.
    proxy_host         = Column(String, nullable=True)
    proxy_port         = Column(String, nullable=True)
    proxy_username     = Column(String, nullable=True)
    proxy_password_enc = Column(String, nullable=True)  # AES encrypted

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    def assign_profile_dir(self) -> None:
        """
        Generate the surrogate id (if not already set) and derive profile_dir
        from it. Uses ONLY the server-generated UUID — never user input.
        Call once at account-creation time, before persisting the row.
        """
        if not self.id:
            self.id = str(uuid.uuid4())
        self.profile_dir = f"{settings.PROFILE_STORAGE_DIR.rstrip('/')}/{self.id}"

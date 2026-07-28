"""
LinkedInAccount model.

Stores one LinkedIn account per row, linked to a platform user via email (FK).
The LinkedIn password is NEVER stored in plaintext — only AES-256-GCM
encrypted ciphertext is persisted (see core.security.encrypt_credential).

Status lifecycle:
    pending_verification  → account added, not yet confirmed working
    active                → Playwright login confirmed, automation can run
    failed                → login failed (bad credentials / checkpoint)
    suspended             → manually disabled by admin or rate-limit breach
"""

import enum
from sqlalchemy import Column, DateTime, Enum as SAEnum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func
from database import Base
 
 
class LinkedInAccountStatus(str, enum.Enum):
    PENDING_VERIFICATION = "pending_verification"
    ACTIVE               = "active"
    VALID                = "valid"
    FAILED               = "failed"
    SUSPENDED            = "suspended"
 
 
class LinkedInAccount(Base):
    __tablename__ = "linkedin_accounts"
 
    owner_email = Column( String, ForeignKey("users.email", ondelete="CASCADE"))
    linkedin_email     = Column(String, nullable=False , primary_key=True, unique=True, index=True)
    encrypted_password = Column(String, nullable=False)
    label              = Column(String, nullable=True)
    status             = Column(SAEnum(LinkedInAccountStatus, name="linkedin_account_status", create_type=False), 
                                nullable=False, default=LinkedInAccountStatus.PENDING_VERIFICATION)
 
    # ── NEW: AES-256-GCM encrypted JSON storage state ──────────────────────────
    # Stores full Playwright storage state (cookies + localStorage) as encrypted JSON.
    # Format (after decrypt): JSON dict with "cookies" and "origins" keys.
    # NEVER return this field in any API response.
    encrypted_storage_state = Column(Text, nullable=True)
    cookies_updated_at = Column(DateTime(timezone=True), nullable=True)
    
    # ── User-Agent used during login (for session consistency) ───────────────
    # LinkedIn rejects sessions if User-Agent changes between login and reuse
    user_agent = Column(String, nullable=True)
 
    # ── Webshare proxy assigned to this account ───────────────────────────────
    proxy_host         = Column(String, nullable=True)
    proxy_port         = Column(String, nullable=True)
    proxy_username     = Column(String, nullable=True)
    proxy_password_enc = Column(String, nullable=True)  # AES encrypted
 
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

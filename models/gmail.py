"""
Gmail connection — SQLAlchemy model.
FILE: models/gmail.py

One row per LinkEasy user (``owner_email`` unique) holding their Google OAuth
connection to a personal Gmail or Google Workspace mailbox:

  * OAuth tokens are persisted AES-256-GCM encrypted only
    (``core.security.encrypt_credential``), mirroring
    ``SocialPlatformConnection`` and ``LinkedInAccount.encrypted_password``.
    The column names say so, so an accidental plaintext write is obvious in
    review.
  * The connected mailbox address is stored in ``account_email`` (a display
    key, never used for filesystem paths or lookups of other tables).
  * ``granted_scopes`` records the OAuth scopes Google actually approved at
    connect time. Gmail offers no token introspection endpoint, so this is a
    local copy of what the callback received — it is shown in the UI so a
    user can see the app never asked for full mailbox access.
  * ``last_checked_at`` is bumped whenever the API talks to Gmail on the
    user's behalf (the "check mail" tick), so the UI can show when the
    mailbox was last polled.

Status lifecycle is implicit in the row: row present = connected. A row whose
tokens can no longer decrypt (encryption key rotated) or whose refresh token
was revoked at Google is reported as "reconnect required" and the user simply
re-runs the OAuth flow, which upserts this same row.
"""

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class GmailConnection(Base):
    __tablename__ = "gmail_connections"
    __table_args__ = (
        # One connected mailbox per user — the same singleton-per-owner model
        # as WhatsApp. This composite unique index also serves the per-owner
        # lookups (owner_email is its leading column).
        UniqueConstraint("owner_email", name="uq_gmail_connections_owner_email"),
        # Admin/ops views may want to find a mailbox across users.
        UniqueConstraint("account_email", name="uq_gmail_connections_account_email"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    # The connected Google account address (lower-cased). Unique per the table
    # constraint above so the same mailbox cannot be linked twice, even across
    # two LinkEasy users (each would otherwise get a live window into the same
    # inbox with only one of the tokens being refreshable).
    account_email = Column(String, nullable=False, default="", server_default="")

    # AES-256-GCM ciphertext ("<nonce_hex>:<ciphertext_hex>") produced by
    # core.security.encrypt_credential — never the raw token.
    encrypted_access_token = Column(Text, nullable=False)
    encrypted_refresh_token = Column(Text, nullable=True)  # NULL: Google issued none
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Scopes Google approved when the user connected, space-separated.
    granted_scopes = Column(Text, nullable=False, default="", server_default="")

    # Snapshot of Gmail's users/me/profile taken at connect/refresh time.
    messages_total = Column(String, nullable=False, default="", server_default="")
    threads_total = Column(String, nullable=False, default="", server_default="")
    # Highest Gmail history id seen; kept so a future push-watch or
    # incremental delta sync has a starting point without an extra profile call.
    history_id = Column(String, nullable=False, default="", server_default="")

    # When the API last called Gmail for this user (the UI's "last checked").
    last_checked_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["GmailConnection"]

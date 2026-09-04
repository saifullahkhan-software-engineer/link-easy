"""
Social post scheduler — SQLAlchemy models.
FILE: models/social_scheduler.py

Tables:
  social_posts                 — a video scheduled for one or more platforms
  social_post_results          — per-platform publish outcome for a post
  social_platform_connections  — a user's OAuth connection to one platform

Ported from the standalone ``social_scheduler/`` service into the main app.
Schema changes relative to the original:

* Multi-tenant. Every row belongs to one platform user via ``owner_email``
  (FK ``users.email`` — the ownership key used by ``linkedin_accounts``,
  ``feed_scroll_jobs`` and ``whatsapp_sessions``; ``users`` has no integer
  id). A platform can therefore be connected once *per user*
  (``(owner_email, platform)`` unique) instead of once per deployment.
* OAuth tokens are persisted AES-256-GCM encrypted only (see
  ``core.security.encrypt_credential``), mirroring
  ``LinkedInAccount.encrypted_password``. The column names say so, so an
  accidental plaintext write is obvious in review.
* Timestamps are timezone-aware and server-generated (``func.now()``) like the
  rest of the schema. The original used the *string* ``"now()"``, which
  SQLAlchemy binds as a literal and asyncpg rejects on every UPDATE.
* ``SocialPost.results`` is eager-loaded (``lazy="selectin"``): a lazy load
  during response serialisation raises ``MissingGreenlet`` under AsyncSession.
"""

import enum
import uuid

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base


class SocialPlatform(str, enum.Enum):
    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    FACEBOOK = "facebook"

class SocialPostStatus(str, enum.Enum):
    PENDING = "pending"      # scheduled, waiting for scheduled_at
    POSTING = "posting"      # claimed by the worker, uploads in progress
    POSTED = "posted"        # every platform succeeded
    FAILED = "failed"        # at least one platform failed
    CANCELLED = "cancelled"  # withdrawn by the user before publishing


class SocialPostResultStatus(str, enum.Enum):
    PENDING = "pending"
    POSTED = "posted"
    FAILED = "failed"


def _uuid() -> str:
    return str(uuid.uuid4())


class SocialPost(Base):
    __tablename__ = "social_posts"
    __table_args__ = (
        # The Beat dispatcher's due-post scan:
        #   WHERE status = 'pending' AND scheduled_at <= now()
        Index("ix_social_posts_status_scheduled_at", "status", "scheduled_at"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    owner_email = Column(
        String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False, index=True
    )

    title = Column(String, nullable=False)
    caption = Column(Text, nullable=False)
    hashtags = Column(Text, nullable=False, default="", server_default="")
    # Server-side path of the uploaded file. Set by the upload endpoint from
    # its own generated filename — never taken from the client — because the
    # worker opens this path and streams it to YouTube/TikTok.
    video_path = Column(String, nullable=False)
    # Public URL of the same file; Instagram fetches the video from here.
    video_url = Column(String, nullable=False)
    thumbnail = Column(String, nullable=False, default="", server_default="")
    platforms = Column(JSON, nullable=False)  # youtube | instagram | tiktok | facebook
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(
        String,
        nullable=False,
        default=SocialPostStatus.PENDING.value,
        server_default=SocialPostStatus.PENDING.value,
    )
    # Optional per-platform overrides; empty string means "use title/caption".
    youtube_title = Column(String, nullable=False, default="", server_default="")
    instagram_caption = Column(Text, nullable=False, default="", server_default="")
    tiktok_caption = Column(Text, nullable=False, default="", server_default="")
    # Structured copy for the platform editor. Each key is a platform and each
    # value may contain title, description and hashtags. The legacy override
    # columns above remain for backwards compatibility with existing posts.
    platform_copy = Column(JSON, nullable=False, default=dict, server_default="{}")
    # Facebook Groups the user wants the Reel shared to. Meta removed the
    # Groups API on 22 Apr 2024, so nothing here is posted by the worker: each
    # entry is a destination the *user* opens after publishing, and the post
    # shows them as a checklist with the caption ready to copy. The entries are
    # snapshotted (name + url) rather than referenced by id, so deleting a
    # saved target never blanks an old post's history.
    facebook_groups = Column(JSON, nullable=False, default=list, server_default="[]")
    # YouTube playlists the Short is added to *after* the upload succeeds
    # (playlistItems.insert per id). Empty list = publish without adding it
    # anywhere. YouTube-only for now: the other three platforms have no
    # equivalent collection a video can be filed into through their APIs.
    youtube_playlist_ids = Column(JSON, nullable=False, default=list, server_default="[]")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    results = relationship(
        "SocialPostResult",
        back_populates="post",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="SocialPostResult.platform",
    )


class SocialPostResult(Base):
    __tablename__ = "social_post_results"
    __table_args__ = (
        # One outcome row per (post, platform). The publisher upserts into it,
        # so two overlapping worker ticks cannot create duplicates.
        UniqueConstraint("post_id", "platform", name="uq_social_post_results_post_platform"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    post_id = Column(
        String, ForeignKey("social_posts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Denormalised from the parent post so per-user history/stat queries and
    # admin views never need a join to establish ownership.
    owner_email = Column(
        String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False, index=True
    )
    platform = Column(String, nullable=False)  # youtube | instagram | tiktok
    status = Column(
        String,
        nullable=False,
        default=SocialPostResultStatus.PENDING.value,
        server_default=SocialPostResultStatus.PENDING.value,
    )
    platform_id = Column(String, nullable=False, default="", server_default="")   # video/media id
    platform_url = Column(String, nullable=False, default="", server_default="")  # link to the post
    error = Column(Text, nullable=False, default="", server_default="")
    # Non-fatal detail about a *successful* publish, e.g. "added to 2 of 3
    # playlists". ``error`` is only rendered for failed rows, so a partial
    # success needs its own column to stay visible in the UI.
    note = Column(Text, nullable=False, default="", server_default="")
    posted_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    post = relationship("SocialPost", back_populates="results")


class SocialPlatformConnection(Base):
    __tablename__ = "social_platform_connections"
    __table_args__ = (
        # One connection per user per platform. This composite unique index
        # also serves the per-owner lookups (owner_email is its leading column).
        UniqueConstraint(
            "owner_email", "platform", name="uq_social_platform_connections_owner_platform"
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    owner_email = Column(String, ForeignKey("users.email", ondelete="CASCADE"), nullable=False)
    platform = Column(String, nullable=False)  # youtube | instagram | tiktok

    # AES-256-GCM ciphertext ("<nonce_hex>:<ciphertext_hex>") produced by
    # core.security.encrypt_credential — never the raw token.
    encrypted_access_token = Column(Text, nullable=False)
    encrypted_refresh_token = Column(Text, nullable=True)  # NULL: platform issued none
    expires_at = Column(DateTime(timezone=True), nullable=True)

    account_name = Column(String, nullable=False, default="", server_default="")
    account_id = Column(String, nullable=False, default="", server_default="")
    # Platform-specific extras, e.g. {"page_id": ...} for Instagram.
    extra_data = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class PlatformCredential(Base):
    """Operator-set OAuth *app* credentials for one scheduler platform.

    Normally platform app credentials come from the environment
    (``YOUTUBE_CLIENT_ID`` … in ``core.config.Settings``). This table lets an
    operator enter them from the settings page instead — a row here *overrides*
    the corresponding environment pair (see services/social/credentials.py).

    The two columns store the provider's identifier and secret pair under
    generic names because each provider spells them differently:

      youtube     → client_id / client_secret
      instagram   → app_id   / app_secret
      tiktok      → client_key / client_secret
      facebook    → app_id   / app_secret

    The identifier is not secret and may be echoed to an operator; the secret
    is write-only (never returned by any API response). Rows are deployment
    global (one per platform), not per user.
    """

    __tablename__ = "platform_credentials"

    platform = Column(String, primary_key=True)  # youtube | instagram | tiktok | facebook
    client_id = Column(String, nullable=False, default="", server_default="")
    client_secret = Column(String, nullable=False, default="", server_default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ShareTarget(Base):
    """A place LinkEasy cannot post to through an API — a Facebook Group.

    Group publishing went away with Meta's Groups API (removed across every
    Graph API version on 22 Apr 2024; ``publish_to_groups`` included), and no
    supported endpoint replaced it. What is left is a person opening the group
    in their own browser and posting. This row only remembers *where*, so the
    upload page can offer a picker instead of a free-text field and the
    published post can render a checklist.

    Nothing here is a credential and nothing is posted automatically: it is a
    bookmark list scoped to one user, which is why it stores a plain URL and a
    display name rather than a token or a group id.
    """

    __tablename__ = "share_targets"
    __table_args__ = (
        # One entry per (user, platform, destination) — the picker must not
        # offer the same group twice.
        UniqueConstraint("owner_email", "platform", "url", name="uq_share_target_owner_platform_url"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    owner_email = Column(
        String,
        ForeignKey("users.email", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Always "facebook" today; kept as a column so another API-less surface
    # (a Telegram channel, a WhatsApp community) does not need a new table.
    platform = Column(String, nullable=False, default="facebook", server_default="facebook", index=True)
    name = Column(String, nullable=False, default="")
    url = Column(String, nullable=False, default="")

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

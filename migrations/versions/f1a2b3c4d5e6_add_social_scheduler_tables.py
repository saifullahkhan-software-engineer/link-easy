"""add social scheduler tables

Revision ID: f1a2b3c4d5e6
Revises: e9d2f1a0b3c4
Create Date: 2026-09-03

Folds the standalone ``social_scheduler/`` service into the main schema.
Three new per-user tables:

  * ``social_posts``                 — a video scheduled for one or more of
                                       YouTube Shorts / Instagram Reels / TikTok.
  * ``social_post_results``          — one publish outcome per (post, platform).
  * ``social_platform_connections``  — the user's OAuth connection to a
                                       platform. Tokens are stored AES-256-GCM
                                       encrypted (core.security), never raw.

Differences from the original standalone schema, on purpose:

  * every table carries ``owner_email`` (FK users.email, CASCADE) — the same
    ownership key as linkedin_accounts / feed_scroll_jobs / whatsapp_sessions;
  * a platform is unique per *user* (``owner_email, platform``) instead of
    per deployment;
  * timestamps are ``timestamptz`` with a real ``now()`` server default (the
    original used the string literal ``"now()"``, which broke every UPDATE).

Every step is guarded so the migration is safe to re-run, matching the
idempotent style of the other files in this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database these
tables usually already exist by the time this revision runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e9d2f1a0b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))


def _timestamps():
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "social_posts"):
        op.create_table(
            "social_posts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("caption", sa.Text(), nullable=False),
            sa.Column("hashtags", sa.Text(), nullable=False, server_default=""),
            sa.Column("video_path", sa.String(), nullable=False),
            sa.Column("video_url", sa.String(), nullable=False),
            sa.Column("thumbnail", sa.String(), nullable=False, server_default=""),
            sa.Column("platforms", sa.JSON(), nullable=False),
            sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("youtube_title", sa.String(), nullable=False, server_default=""),
            sa.Column("instagram_caption", sa.Text(), nullable=False, server_default=""),
            sa.Column("tiktok_caption", sa.Text(), nullable=False, server_default=""),
            *_timestamps(),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _has_index(bind, "social_posts", "ix_social_posts_owner_email"):
        op.create_index("ix_social_posts_owner_email", "social_posts", ["owner_email"])
    if not _has_index(bind, "social_posts", "ix_social_posts_status_scheduled_at"):
        # The Beat dispatcher's due scan: status = 'pending' AND scheduled_at <= now()
        op.create_index(
            "ix_social_posts_status_scheduled_at",
            "social_posts",
            ["status", "scheduled_at"],
        )

    if not _has_table(bind, "social_post_results"):
        op.create_table(
            "social_post_results",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("post_id", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("platform_id", sa.String(), nullable=False, server_default=""),
            sa.Column("platform_url", sa.String(), nullable=False, server_default=""),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
            *_timestamps(),
            sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            # The publisher upserts per (post, platform); overlapping worker
            # ticks must not be able to create a second outcome row.
            sa.UniqueConstraint(
                "post_id", "platform", name="uq_social_post_results_post_platform"
            ),
        )
    if not _has_index(bind, "social_post_results", "ix_social_post_results_post_id"):
        op.create_index("ix_social_post_results_post_id", "social_post_results", ["post_id"])
    if not _has_index(bind, "social_post_results", "ix_social_post_results_owner_email"):
        op.create_index(
            "ix_social_post_results_owner_email", "social_post_results", ["owner_email"]
        )

    if not _has_table(bind, "social_platform_connections"):
        op.create_table(
            "social_platform_connections",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("encrypted_access_token", sa.Text(), nullable=False),
            sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("account_name", sa.String(), nullable=False, server_default=""),
            sa.Column("account_id", sa.String(), nullable=False, server_default=""),
            sa.Column("extra_data", sa.JSON(), nullable=True),
            *_timestamps(),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            # One connection per user per platform (was: one per deployment).
            sa.UniqueConstraint(
                "owner_email",
                "platform",
                name="uq_social_platform_connections_owner_platform",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    # Children first: social_post_results references social_posts.
    for table in ("social_post_results", "social_platform_connections", "social_posts"):
        if _has_table(bind, table):
            op.drop_table(table)

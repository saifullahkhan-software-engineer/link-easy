"""add social post content_kind and media_kind

Revision ID: b4c8d1e2f3a0
Revises: a7b8c9d0e1f2
Create Date: 2026-09-06

The scheduler now has two composers:

  * Shorts — vertical clips (YouTube Shorts / Reels / TikTok)
  * Posts  — regular feed posts, which may be a photo or a video

``content_kind`` records which composer created the row (``shorts`` | ``post``).
``media_kind`` records what file is stored at ``video_path`` (``video`` | ``image``).
Existing rows predate both columns and are treated as short-form video.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4c8d1e2f3a0"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "social_posts"):
        return
    if not _has_column(bind, "social_posts", "content_kind"):
        op.add_column(
            "social_posts",
            sa.Column("content_kind", sa.String(), nullable=False, server_default="shorts"),
        )
    if not _has_column(bind, "social_posts", "media_kind"):
        op.add_column(
            "social_posts",
            sa.Column("media_kind", sa.String(), nullable=False, server_default="video"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "social_posts"):
        return
    if _has_column(bind, "social_posts", "media_kind"):
        op.drop_column("social_posts", "media_kind")
    if _has_column(bind, "social_posts", "content_kind"):
        op.drop_column("social_posts", "content_kind")

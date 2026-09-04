"""add youtube playlist targets and per-platform publish notes

Revision ID: f2a3b4c5d6e7
Revises: e8f7a6b5c4d3
Create Date: 2026-09-04

Two additions for the scheduler:

* ``social_posts.youtube_playlist_ids`` — the playlists a Short is filed into
  after it uploads. Empty (the default) keeps today's behaviour, so posts
  created before this change publish exactly as they did.
* ``social_post_results.note`` — a non-fatal remark about a *successful*
  publish, such as "published, but 1 playlist could not be updated". The
  existing ``error`` column is only surfaced by the UI for failed rows, so a
  partial success would otherwise be invisible.

Both are idempotent (guarded by inspector checks) like the rest of this
project's migrations, so a re-run or a partially migrated database is safe.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e8f7a6b5c4d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "social_posts") and not _has_column(bind, "social_posts", "youtube_playlist_ids"):
        op.add_column(
            "social_posts",
            sa.Column("youtube_playlist_ids", sa.JSON(), nullable=False, server_default="[]"),
        )
    if _has_table(bind, "social_post_results") and not _has_column(bind, "social_post_results", "note"):
        op.add_column(
            "social_post_results",
            sa.Column("note", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "social_post_results") and _has_column(bind, "social_post_results", "note"):
        op.drop_column("social_post_results", "note")
    if _has_table(bind, "social_posts") and _has_column(bind, "social_posts", "youtube_playlist_ids"):
        op.drop_column("social_posts", "youtube_playlist_ids")

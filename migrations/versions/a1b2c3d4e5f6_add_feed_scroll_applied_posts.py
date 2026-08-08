"""add feed scroll applied posts table

Revision ID: a1b2c3d4e5f6
Revises: 8c4f1a2b3d4e
Create Date: 2026-08-08 00:00:00.000000

Adds `feed_scroll_applied_posts` table to permanently store posts marked as
applied by users so future feed scroll scans crossmatch and avoid duplicate posts.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "8c4f1a2b3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE_NAME = "feed_scroll_applied_posts"


def _table_exists(bind, table_name: str) -> bool:
    return sa.inspect(bind).has_table(table_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("feed_scroll_job_id", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("post_urn", sa.String(), nullable=True),
            sa.Column("post_url", sa.String(), nullable=False),
            sa.Column("author_name", sa.String(), nullable=True),
            sa.Column("author_first_name", sa.String(), nullable=True),
            sa.Column("author_last_name", sa.String(), nullable=True),
            sa.Column("author_profile_url", sa.String(), nullable=False),
            sa.Column("connection_degree", sa.String(), nullable=True),
            sa.Column("post_time", sa.String(), nullable=True),
            sa.Column("post_text", sa.Text(), nullable=True),
            sa.Column("score", sa.Float(), nullable=False, server_default="0"),
            sa.Column("matched_terms", sa.JSON(), nullable=True),
            sa.Column("scan_batch_id", sa.String(), nullable=True),
            sa.Column("applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["feed_scroll_job_id"], ["feed_scroll_jobs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_feed_scroll_applied_posts_feed_scroll_job_id", TABLE_NAME, ["feed_scroll_job_id"])
        op.create_index("ix_feed_scroll_applied_posts_owner_email", TABLE_NAME, ["owner_email"])
        op.create_index("ix_feed_scroll_applied_posts_post_urn", TABLE_NAME, ["post_urn"])


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, TABLE_NAME):
        op.drop_table(TABLE_NAME)

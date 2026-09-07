"""add per-account YouTube playlist selections

Revision ID: i4j5k6l7m8n
Revises: h3i4j5k6l7m8
Create Date: 2026-09-07

``SocialPost.youtube_playlists_by_account`` stores the playlist IDs selected
for each connected YouTube channel.  It was added to the ORM model and API
payloads, but older ``social_posts`` tables do not receive new ORM columns
from ``Base.metadata.create_all()``.  Without this migration, selecting any
SocialPost (including ``GET /posts``) fails with ``UndefinedColumnError``.

The inspection guard makes the repair safe for new databases, partially
migrated databases, and deployments where the column was added manually.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "i4j5k6l7m8n"
down_revision: Union[str, Sequence[str], None] = "h3i4j5k6l7m8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POSTS_TABLE = "social_posts"
COLUMN_NAME = "youtube_playlists_by_account"


def _has_column(bind, table: str, column: str) -> bool:
    """Return whether ``table.column`` exists without assuming a fresh DB."""
    inspector = sa.inspect(bind)
    return inspector.has_table(table) and any(
        item["name"] == column for item in inspector.get_columns(table)
    )


def upgrade() -> None:
    """Add an empty JSON mapping for existing and newly created posts."""
    bind = op.get_bind()
    if not _has_column(bind, POSTS_TABLE, COLUMN_NAME):
        op.add_column(
            POSTS_TABLE,
            sa.Column(COLUMN_NAME, sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, POSTS_TABLE, COLUMN_NAME):
        op.drop_column(POSTS_TABLE, COLUMN_NAME)

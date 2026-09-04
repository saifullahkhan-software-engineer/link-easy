"""add structured per-platform copy and direct-upload support

Revision ID: e8f7a6b5c4d3
Revises: d5e1f2a3b4c9
Create Date: 2026-09-04

The upload editor needs a separate title, description and hashtag value for
each connected platform. Keep the existing legacy override columns intact so
posts created before this change continue to publish unchanged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f7a6b5c4d3"
down_revision: Union[str, Sequence[str], None] = "d5e1f2a3b4c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "social_posts") and not _has_column(bind, "social_posts", "platform_copy"):
        op.add_column(
            "social_posts",
            sa.Column("platform_copy", sa.JSON(), nullable=False, server_default="{}"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "social_posts") and _has_column(bind, "social_posts", "platform_copy"):
        op.drop_column("social_posts", "platform_copy")

"""add facebook group share targets and the per-post group selection

Revision ID: a9b8c7d6e5f4
Revises: f2a3b4c5d6e7
Create Date: 2026-09-04

Meta removed the Facebook Groups API on 22 Apr 2024, so a Reel cannot be
published into a group by the worker. What can be offered is a checklist: the
user picks the groups on the upload page and, once the Reel is published, the
post lists them with the caption ready to copy and a link to each group.

* ``share_targets`` — one user's saved destinations (name + URL). A bookmark
  list, not a credential: nothing here is ever posted automatically.
* ``social_posts.facebook_groups`` — the destinations chosen for that post,
  snapshotted as ``[{"name", "url"}]`` so deleting a saved target never blanks
  an older post. Empty (the default) keeps today's behaviour.

Idempotent like the rest of this project's migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(column["name"] == name for column in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "share_targets"):
        op.create_table(
            "share_targets",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_email", sa.String(), nullable=False),
            sa.Column("platform", sa.String(), nullable=False, server_default="facebook"),
            sa.Column("name", sa.String(), nullable=False, server_default=""),
            sa.Column("url", sa.String(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["owner_email"], ["users.email"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("owner_email", "platform", "url", name="uq_share_target_owner_platform_url"),
        )
        op.create_index("ix_share_targets_owner_email", "share_targets", ["owner_email"])
        op.create_index("ix_share_targets_platform", "share_targets", ["platform"])
    if _has_table(bind, "social_posts") and not _has_column(bind, "social_posts", "facebook_groups"):
        op.add_column(
            "social_posts",
            sa.Column("facebook_groups", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "social_posts") and _has_column(bind, "social_posts", "facebook_groups"):
        op.drop_column("social_posts", "facebook_groups")
    if _has_table(bind, "share_targets"):
        op.drop_index("ix_share_targets_platform", table_name="share_targets")
        op.drop_index("ix_share_targets_owner_email", table_name="share_targets")
        op.drop_table("share_targets")

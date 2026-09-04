"""add user_deletion_tokens table

Revision ID: d5e1f2a3b4c9
Revises: c8d4e6f1a2b3
Create Date: 2026-09-04

One-time signed tokens that authorise deleting an account. Mirror of
``password_reset_tokens``: the token row is consumed when the deletion link
is used, so a link cannot be replayed, and rows can be invalidated by
deleting them. Deletion is always email-confirmed — a bare email address is
never enough to delete an account.

Guarded so the migration is safe to re-run, matching the idempotent style of
the other files in this directory. The startup path runs
``Base.metadata.create_all`` before Alembic, so on a fresh database this table
usually already exists by the time this revision runs.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d5e1f2a3b4c9"
down_revision: Union[str, Sequence[str], None] = "c8d4e6f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_table(bind, "user_deletion_tokens"):
        op.create_table(
            "user_deletion_tokens",
            sa.Column("token_id", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("token_id"),
        )
        op.create_index(
            "ix_user_deletion_tokens_email",
            "user_deletion_tokens",
            ["email"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "user_deletion_tokens"):
        op.drop_index("ix_user_deletion_tokens_email", table_name="user_deletion_tokens")
        op.drop_table("user_deletion_tokens")
